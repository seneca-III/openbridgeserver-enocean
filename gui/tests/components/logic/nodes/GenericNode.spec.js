import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const HANDLE_STUB = { template: '<div class="handle" :data-type="type" :data-id="id" />', props: ['type', 'id', 'position', 'style'] }

const removeNodesMock  = vi.fn()
const updateNodeDataMock = vi.fn()

vi.mock('@vue-flow/core', () => ({
  Handle:     HANDLE_STUB,
  Position:   { Left: 'left', Right: 'right' },
  useVueFlow: () => ({ removeNodes: removeNodesMock, updateNodeData: updateNodeDataMock }),
}))

async function mountGN(type, data = {}, extraProps = {}) {
  const { default: GenericNode } = await import('@/components/logic/nodes/GenericNode.vue')
  return mount(GenericNode, {
    props: { id: 'gn-1', type, data, ...extraProps },
    global: { stubs: { Handle: HANDLE_STUB } },
  })
}

describe('GenericNode — label from NODE_DEFS', () => {
  beforeEach(() => { removeNodesMock.mockClear(); updateNodeDataMock.mockClear() })

  it('shows "Festwert" for const_value', async () => {
    const w = await mountGN('const_value')
    await flushPromises()
    expect(w.find('.gn-title').text()).toBe('Festwert')
  })

  it('shows "AND" for and type', async () => {
    const w = await mountGN('and')
    await flushPromises()
    expect(w.find('.gn-title').text()).toBe('AND')
  })

  it('falls back to type string for unknown type', async () => {
    const w = await mountGN('mystery_node')
    await flushPromises()
    expect(w.find('.gn-title').text()).toBe('mystery_node')
  })
})

describe('GenericNode — handles', () => {
  it('renders no target handles for const_value (no inputs)', async () => {
    const w = await mountGN('const_value')
    await flushPromises()
    const targets = w.findAll('.handle').filter(h => h.attributes('data-type') === 'target')
    expect(targets.length).toBe(0)
  })

  it('renders 1 source handle for const_value', async () => {
    const w = await mountGN('const_value')
    await flushPromises()
    const sources = w.findAll('.handle').filter(h => h.attributes('data-type') === 'source')
    expect(sources.length).toBe(1)
  })

  it('renders 2 target handles for and type', async () => {
    const w = await mountGN('and')
    await flushPromises()
    const targets = w.findAll('.handle').filter(h => h.attributes('data-type') === 'target')
    expect(targets.length).toBe(2)
  })

  it('renders dynamic input count for AND gate (input_count=4)', async () => {
    const w = await mountGN('and', { input_count: 4 })
    await flushPromises()
    const targets = w.findAll('.handle').filter(h => h.attributes('data-type') === 'target')
    expect(targets.length).toBe(4)
  })
})

describe('GenericNode — summary', () => {
  it('shows summary for const_value', async () => {
    const w = await mountGN('const_value', { data_type: 'number', value: '42' })
    await flushPromises()
    expect(w.find('.gn-summary').text()).toContain('42')
  })

  it('shows formula for math_formula', async () => {
    const w = await mountGN('math_formula', { formula: 'a * 2' })
    await flushPromises()
    expect(w.find('.gn-summary').text()).toContain('a * 2')
  })

  it('shows compare summary: A > B by default', async () => {
    const w = await mountGN('compare')
    await flushPromises()
    expect(w.find('.gn-summary').text()).toBe('A > B')
  })

  it('shows delay_s for timer_delay', async () => {
    const w = await mountGN('timer_delay', { delay_s: 5 })
    await flushPromises()
    expect(w.find('.gn-summary').text()).toContain('5')
  })
})

describe('GenericNode — debug band', () => {
  it('shows debug band when data._dbg is set', async () => {
    const w = await mountGN('and', { _dbg: 'true' })
    await flushPromises()
    expect(w.find('[data-testid="debug-band"]').exists()).toBe(true)
    expect(w.find('[data-testid="debug-band"]').text()).toBe('true')
  })

  it('hides debug band when no _dbg', async () => {
    const w = await mountGN('and')
    await flushPromises()
    expect(w.find('[data-testid="debug-band"]').exists()).toBe(false)
  })
})

describe('GenericNode — gate node negate', () => {
  it('shows negate buttons for AND gate inputs', async () => {
    const w = await mountGN('and')
    await flushPromises()
    const negateBtns = w.findAll('.gn-port-negate')
    expect(negateBtns.length).toBeGreaterThanOrEqual(2)
  })

  it('calls updateNodeData when negate button is clicked', async () => {
    const w = await mountGN('and', { negate_in1: false })
    await flushPromises()
    const negateBtns = w.findAll('.gn-port-negate')
    await negateBtns[0].trigger('click')
    expect(updateNodeDataMock).toHaveBeenCalledWith('gn-1', expect.objectContaining({ negate_in1: true }))
  })
})

describe('GenericNode — delete', () => {
  it('calls removeNodes on delete button click', async () => {
    const w = await mountGN('and')
    await flushPromises()
    await w.find('.gn-del').trigger('click')
    expect(removeNodesMock).toHaveBeenCalledWith(['gn-1'])
  })
})
