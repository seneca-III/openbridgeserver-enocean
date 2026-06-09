import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

beforeEach(() => {
  vi.resetModules()
  const storage = {
    getItem: vi.fn().mockReturnValue(null),
    setItem: vi.fn(),
    removeItem: vi.fn(),
    clear: vi.fn(),
  }
  Object.defineProperty(window, 'localStorage', {
    value: storage,
    configurable: true,
  })
  Object.defineProperty(globalThis, 'localStorage', {
    value: storage,
    configurable: true,
  })
  vi.doMock('vue-router', () => ({
    useRoute: () => ({ query: {} }),
  }))
  vi.doMock('@vue-flow/core', () => ({
    VueFlow: { template: '<div><slot /></div>' },
    Handle: { template: '<span />' },
    Position: { Left: 'left', Right: 'right', Top: 'top', Bottom: 'bottom' },
    useVueFlow: () => ({ project: (point) => point }),
    addEdge: (edge, edges) => [...edges, edge],
  }))
  vi.doMock('@vue-flow/background', () => ({
    Background: { template: '<div />' },
  }))
  vi.doMock('@vue-flow/controls', () => ({
    Controls: { template: '<div />' },
  }))
  vi.doMock('@vue-flow/minimap', () => ({
    MiniMap: { template: '<div />' },
  }))
})

afterEach(() => {
  vi.doUnmock('@/api/client')
  vi.doUnmock('vue-router')
  vi.doUnmock('@vue-flow/core')
  vi.doUnmock('@vue-flow/background')
  vi.doUnmock('@vue-flow/controls')
  vi.doUnmock('@vue-flow/minimap')
})

async function mountLogicView({ isAdmin }) {
  const logicApi = {
    nodeTypes: vi.fn().mockResolvedValue({ data: [] }),
    listGraphs: vi.fn().mockResolvedValue({ data: [] }),
    createGraph: vi.fn().mockResolvedValue({
      data: { id: 'graph-new', name: 'New', description: '', enabled: true, flow_data: { nodes: [], edges: [] } },
    }),
  }
  vi.doMock('@/api/client', () => ({ logicApi }))

  const pinia = createPinia()
  setActivePinia(pinia)
  const { useAuthStore } = await import('@/stores/auth')
  useAuthStore().user = { id: 'u1', username: isAdmin ? 'admin' : 'viewer', is_admin: isAdmin }

  const mod = await import('@/views/LogicView.vue')
  const wrapper = mount(mod.default, {
    global: {
      plugins: [pinia],
      stubs: {
        NodePalette: true,
        NodeConfigPanel: true,
        Modal: { template: '<div><slot /><slot name="footer" /></div>' },
        ConfirmDialog: true,
        Spinner: { template: '<span />' },
      },
    },
    attachTo: document.body,
  })
  await flushPromises()
  return { wrapper, logicApi }
}

describe('LogicView auth gates', () => {
  it('hides graph creation for non-admin users', async () => {
    const { wrapper, logicApi } = await mountLogicView({ isAdmin: false })

    expect(wrapper.text()).not.toContain('+ Neu')
    expect(logicApi.createGraph).not.toHaveBeenCalled()
  })
})
