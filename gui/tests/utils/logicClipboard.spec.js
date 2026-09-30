/**
 * Tests for the logic-editor node copy/paste helpers (#1084).
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { cloneSelectionForClipboard, remapClipboardForPaste } from '@/utils/logicClipboard'

function makeNode(id, overrides = {}) {
  return { id, type: 'and', position: { x: 10, y: 20 }, data: { input_count: 2 }, ...overrides }
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

describe('cloneSelectionForClipboard', () => {
  it('returns null when nothing is selected', () => {
    const nodes = [makeNode('n1'), makeNode('n2')]
    expect(cloneSelectionForClipboard(nodes, [])).toBeNull()
  })

  it('clones a single selected node', () => {
    const nodes = [makeNode('n1', { selected: true }), makeNode('n2')]
    const result = cloneSelectionForClipboard(nodes, [])
    expect(result.nodes).toHaveLength(1)
    expect(result.nodes[0]).toMatchObject({ id: 'n1', type: 'and', position: { x: 10, y: 20 }, data: { input_count: 2 } })
    expect(result.edges).toEqual([])
  })

  it('keeps internal edges and drops edges leaving the selection', () => {
    const nodes = [
      makeNode('n1', { selected: true }),
      makeNode('n2', { selected: true }),
      makeNode('n3'),
    ]
    const edges = [
      { id: 'e1', source: 'n1', target: 'n2', sourceHandle: null, targetHandle: null },
      // Outgoing boundary edge: selected source, unselected target.
      { id: 'e2', source: 'n2', target: 'n3', sourceHandle: null, targetHandle: null },
      // Incoming boundary edge: unselected source, selected target — the
      // `selectedIds.has(e.source) && ...` check must fail on the *source*
      // side here rather than short-circuiting past a selected one.
      { id: 'e3', source: 'n3', target: 'n1', sourceHandle: null, targetHandle: null },
    ]
    const result = cloneSelectionForClipboard(nodes, edges)
    expect(result.nodes).toHaveLength(2)
    expect(result.edges).toHaveLength(1)
    expect(result.edges[0]).toMatchObject({ source: 'n1', target: 'n2' })
  })

  it('strips transient debug fields from node data', () => {
    const nodes = [makeNode('n1', { selected: true, data: { input_count: 2, _dbg: '= 1', _dbg_title: 'value=1' } })]
    const result = cloneSelectionForClipboard(nodes, [])
    expect(result.nodes[0].data).toEqual({ input_count: 2 })
  })

  it('treats a null/undefined node data as an empty object instead of throwing', () => {
    const nodes = [makeNode('n1', { selected: true, data: null }), makeNode('n2', { selected: true, data: undefined })]
    const result = cloneSelectionForClipboard(nodes, [])
    expect(result.nodes[0].data).toEqual({})
    expect(result.nodes[1].data).toEqual({})
  })

  it('deep-clones node data so mutating the source does not affect the clipboard', () => {
    const source = makeNode('n1', { selected: true, data: { nested: { value: 1 } } })
    const result = cloneSelectionForClipboard([source], [])
    source.data.nested.value = 99
    expect(result.nodes[0].data.nested.value).toBe(1)
  })

  it('returns null instead of throwing when nodes is null or undefined', () => {
    expect(cloneSelectionForClipboard(null, [])).toBeNull()
    expect(cloneSelectionForClipboard(undefined, [])).toBeNull()
  })

  it('treats a null/undefined edges argument as no internal edges', () => {
    const nodes = [makeNode('n1', { selected: true })]
    expect(cloneSelectionForClipboard(nodes, null).edges).toEqual([])
    expect(cloneSelectionForClipboard(nodes, undefined).edges).toEqual([])
  })
})

describe('remapClipboardForPaste', () => {
  it('returns null for an empty clipboard', () => {
    expect(remapClipboardForPaste(null)).toBeNull()
  })

  it('assigns fresh ids to nodes and rewires edges through the id map', () => {
    const clipboard = {
      nodes: [makeNode('n1'), makeNode('n2')],
      edges: [{ id: 'e1', source: 'n1', target: 'n2', sourceHandle: 'a', targetHandle: 'b' }],
    }
    const result = remapClipboardForPaste(clipboard, 0)
    const [p1, p2] = result.nodes
    expect(p1.id).not.toBe('n1')
    expect(p2.id).not.toBe('n2')
    expect(result.edges[0].source).toBe(p1.id)
    expect(result.edges[0].target).toBe(p2.id)
    expect(result.edges[0].sourceHandle).toBe('a')
    expect(result.edges[0].targetHandle).toBe('b')
  })

  it('produces different ids on repeated remaps of the same clipboard', () => {
    const clipboard = { nodes: [makeNode('n1')], edges: [] }
    const first  = remapClipboardForPaste(clipboard, 0)
    const second = remapClipboardForPaste(clipboard, 1)
    expect(first.nodes[0].id).not.toBe(second.nodes[0].id)
  })

  it('offsets node positions further apart for later paste indices', () => {
    const clipboard = { nodes: [makeNode('n1', { position: { x: 0, y: 0 } })], edges: [] }
    const first  = remapClipboardForPaste(clipboard, 0)
    const second = remapClipboardForPaste(clipboard, 1)
    expect(second.nodes[0].position.x).toBeGreaterThan(first.nodes[0].position.x)
    expect(second.nodes[0].position.y).toBeGreaterThan(first.nodes[0].position.y)
  })

  it('anchors the pasted group at targetCenter while preserving relative layout', () => {
    // Regression: pasting into a sheet whose viewport is fitted around a
    // different graph-coordinate region than the source must not leave the
    // pasted blocks outside the destination's visible viewport.
    const clipboard = {
      nodes: [
        makeNode('n1', { position: { x: 0, y: 0 } }),
        makeNode('n2', { position: { x: 100, y: 0 } }),
      ],
      edges: [],
    }
    const result = remapClipboardForPaste(clipboard, 0, { x: 10000, y: 10000 })
    const [p1, p2] = result.nodes
    // Bounding-box center (0,0)-(100,0) is (50, 0); it must land at (10000,
    // 10000) plus the usual pasteIndex=0 stack offset (40) on top.
    expect(p1.position.x + (p2.position.x - p1.position.x) / 2).toBe(10040)
    expect(p1.position.y + (p2.position.y - p1.position.y) / 2).toBe(10040)
    // Relative layout between the two nodes is unchanged.
    expect(p2.position.x - p1.position.x).toBe(100)
    expect(p2.position.y - p1.position.y).toBe(0)
  })

  it('ignores targetCenter when it is not provided, keeping the plain stack offset', () => {
    const clipboard = { nodes: [makeNode('n1', { position: { x: 0, y: 0 } })], edges: [] }
    const withoutTarget = remapClipboardForPaste(clipboard, 0)
    const withNullTarget = remapClipboardForPaste(clipboard, 0, null)
    expect(withoutTarget.nodes[0].position).toEqual(withNullTarget.nodes[0].position)
  })

  it('ignores a truthy targetCenter when the clipboard has no nodes, keeping the plain stack offset', () => {
    // An empty clipboard can't happen via cloneSelectionForClipboard (it
    // returns null for an empty selection), but remapClipboardForPaste is a
    // separate exported function and must not divide by an empty bounding
    // box (Math.min()/Math.max() of no arguments is Infinity/-Infinity).
    const clipboard = { nodes: [], edges: [] }
    const result = remapClipboardForPaste(clipboard, 0, { x: 10000, y: 10000 })
    expect(result.nodes).toEqual([])
  })

  it('treats a null/undefined clipboard node data as an empty object instead of throwing', () => {
    const clipboard = { nodes: [makeNode('n1', { data: null }), makeNode('n2', { data: undefined })], edges: [] }
    const result = remapClipboardForPaste(clipboard, 0)
    expect(result.nodes[0].data).toEqual({})
    expect(result.nodes[1].data).toEqual({})
  })

  it('marks pasted nodes as selected so they can be dragged as a group', () => {
    const clipboard = { nodes: [makeNode('n1'), makeNode('n2')], edges: [] }
    const result = remapClipboardForPaste(clipboard, 0)
    expect(result.nodes.every(n => n.selected === true)).toBe(true)
  })
})

describe('remapClipboardForPaste — id generation outside a secure context', () => {
  // crypto.randomUUID() is unavailable (or throws) when OBS is opened over
  // plain http:// from another machine — a normal LAN deployment. These
  // exercise the crypto.getRandomValues()/Math.random() fallback paths.
  const realGetRandomValues = crypto.getRandomValues.bind(crypto)

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('falls back to crypto.getRandomValues when randomUUID is missing', () => {
    vi.stubGlobal('crypto', { getRandomValues: realGetRandomValues })
    const clipboard = { nodes: [makeNode('n1'), makeNode('n2')], edges: [] }
    const result = remapClipboardForPaste(clipboard, 0)
    expect(result.nodes[0].id).toMatch(UUID_RE)
    expect(result.nodes[0].id).not.toBe(result.nodes[1].id)
  })

  it('falls back when randomUUID throws instead of being missing', () => {
    vi.stubGlobal('crypto', {
      randomUUID: () => { throw new DOMException('not allowed outside a secure context') },
      getRandomValues: realGetRandomValues,
    })
    const clipboard = { nodes: [makeNode('n1')], edges: [] }
    const result = remapClipboardForPaste(clipboard, 0)
    expect(result.nodes[0].id).toMatch(UUID_RE)
  })

  it('falls back to Math.random when crypto is entirely unavailable', () => {
    vi.stubGlobal('crypto', undefined)
    const clipboard = { nodes: [makeNode('n1'), makeNode('n2')], edges: [] }
    const result = remapClipboardForPaste(clipboard, 0)
    expect(result.nodes[0].id).toMatch(UUID_RE)
    expect(result.nodes[0].id).not.toBe(result.nodes[1].id)
  })

  it('falls back to Math.random when crypto exists but getRandomValues is also unavailable', () => {
    // Distinct from the "crypto entirely unavailable" case above: here the
    // `typeof crypto !== 'undefined'` branch is entered, but neither
    // randomUUID nor getRandomValues exist on it — a real gap that a
    // missing-crypto-object test alone would never reach.
    vi.stubGlobal('crypto', {})
    const clipboard = { nodes: [makeNode('n1'), makeNode('n2')], edges: [] }
    const result = remapClipboardForPaste(clipboard, 0)
    expect(result.nodes[0].id).toMatch(UUID_RE)
    expect(result.nodes[0].id).not.toBe(result.nodes[1].id)
  })
})
