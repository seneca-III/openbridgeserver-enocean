const PASTE_OFFSET_STEP = 40

/**
 * crypto.randomUUID() throws/is undefined outside secure contexts (e.g. OBS
 * opened over plain http:// from another machine, which is a normal LAN
 * deployment for this app) — fall back to crypto.getRandomValues(), which
 * has no such restriction, and finally to Math.random() for environments
 * without a crypto object at all. Not security-sensitive: these ids only
 * need to be unique within a single graph.
 */
function generateId() {
  if (typeof crypto !== 'undefined') {
    if (typeof crypto.randomUUID === 'function') {
      try {
        return crypto.randomUUID()
      } catch {
        // Some browsers expose randomUUID but throw outside a secure context
        // instead of omitting it — fall through to getRandomValues().
      }
    }
    if (typeof crypto.getRandomValues === 'function') {
      const bytes = crypto.getRandomValues(new Uint8Array(16))
      bytes[6] = (bytes[6] & 0x0f) | 0x40
      bytes[8] = (bytes[8] & 0x3f) | 0x80
      const hex = Array.from(bytes, b => b.toString(16).padStart(2, '0'))
      return `${hex.slice(0, 4).join('')}-${hex.slice(4, 6).join('')}-${hex.slice(6, 8).join('')}-${hex.slice(8, 10).join('')}-${hex.slice(10, 16).join('')}`
    }
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = (Math.random() * 16) | 0
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16)
  })
}

function stripDebugFields(data) {
  // eslint-disable-next-line no-unused-vars
  const { _dbg, _dbg_title, ...rest } = data
  return rest
}

/**
 * Build a clipboard payload from the currently selected Vue Flow nodes.
 * Only edges whose source and target are both part of the selection are kept.
 * Returns null when nothing is selected.
 */
export function cloneSelectionForClipboard(nodes, edges) {
  const selected = (nodes ?? []).filter(n => n.selected)
  if (selected.length === 0) return null

  const selectedIds = new Set(selected.map(n => n.id))
  const clonedNodes = selected.map(n => ({
    id: n.id,
    type: n.type,
    position: { ...n.position },
    data: stripDebugFields(JSON.parse(JSON.stringify(n.data ?? {}))),
  }))
  const clonedEdges = (edges ?? [])
    .filter(e => selectedIds.has(e.source) && selectedIds.has(e.target))
    .map(e => ({ id: e.id, source: e.source, target: e.target, sourceHandle: e.sourceHandle, targetHandle: e.targetHandle }))

  return { nodes: clonedNodes, edges: clonedEdges }
}

/**
 * Produce a pastable copy of a clipboard payload: fresh ids for every node/edge,
 * edges rewired through the id map, node positions offset so repeated pastes of
 * the same clipboard don't stack exactly on top of each other, and the pasted
 * nodes marked `selected` so they can be dragged as a group right away.
 *
 * `targetCenter` (flow-space {x, y}) anchors the pasted group's bounding-box
 * center there while preserving the group's relative layout — without it,
 * pasting into a sheet whose viewport is fitted around a different
 * graph-coordinate region than the source (e.g. copy near (0, 0), paste into
 * a sheet fitted around (10000, 10000)) would place the pasted nodes outside
 * the destination's visible viewport.
 */
export function remapClipboardForPaste(clipboard, pasteIndex = 0, targetCenter = null) {
  if (!clipboard) return null

  const idMap = new Map(clipboard.nodes.map(n => [n.id, generateId()]))
  // Small per-repeat-paste stagger so identical clipboards pasted several
  // times in a row don't stack exactly on top of each other.
  const stackOffset = PASTE_OFFSET_STEP * (pasteIndex + 1)

  let dx = stackOffset
  let dy = stackOffset
  if (targetCenter && clipboard.nodes.length) {
    const xs = clipboard.nodes.map(n => n.position.x)
    const ys = clipboard.nodes.map(n => n.position.y)
    const groupCenterX = (Math.min(...xs) + Math.max(...xs)) / 2
    const groupCenterY = (Math.min(...ys) + Math.max(...ys)) / 2
    dx = targetCenter.x - groupCenterX + stackOffset
    dy = targetCenter.y - groupCenterY + stackOffset
  }

  const nodes = clipboard.nodes.map(n => ({
    id: idMap.get(n.id),
    type: n.type,
    position: { x: n.position.x + dx, y: n.position.y + dy },
    data: JSON.parse(JSON.stringify(n.data ?? {})),
    selected: true,
  }))
  const edges = clipboard.edges.map(e => ({
    id: generateId(),
    source: idMap.get(e.source),
    target: idMap.get(e.target),
    sourceHandle: e.sourceHandle,
    targetHandle: e.targetHandle,
  }))

  return { nodes, edges }
}
