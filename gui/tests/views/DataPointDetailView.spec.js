import { mount, flushPromises } from '@vue/test-utils'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import DataPointDetailView from '@/views/DataPointDetailView.vue'

const apiMocks = vi.hoisted(() => ({
  dpApi: {
    get: vi.fn(),
    listBindings: vi.fn(),
    writeValue: vi.fn(),
  },
  logicApi: {
    datapointUsages: vi.fn(),
  },
  systemApi: {
    datatypes: vi.fn(),
  },
}))

vi.mock('@/api/client', () => apiMocks)

function mountView() {
  return mount(DataPointDetailView, {
    props: { id: 'dp-internal' },
    global: {
      stubs: {
        RouterLink: { template: '<a><slot /></a>' },
        DataPointHierarchyCard: { template: '<div />' },
        DataPointForm: { template: '<div />' },
        BindingForm: { template: '<div />' },
        Modal: { template: '<div v-if="modelValue"><slot /></div>', props: ['modelValue'] },
        ConfirmDialog: { template: '<div />' },
      },
    },
  })
}

describe('DataPointDetailView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMocks.systemApi.datatypes.mockResolvedValue({ data: [{ name: 'FLOAT' }] })
    apiMocks.dpApi.get.mockResolvedValue({
      data: {
        id: 'dp-internal',
        name: 'Internal Temperature',
        data_type: 'FLOAT',
        unit: '°C',
        tags: [],
        mqtt_topic: 'dp/dp-internal/value',
        mqtt_alias: null,
        persist_value: true,
        record_history: true,
        value: null,
        quality: 'uncertain',
        created_at: '2026-06-11T10:00:00+00:00',
        updated_at: '2026-06-11T10:00:00+00:00',
      },
    })
    apiMocks.dpApi.listBindings.mockResolvedValue({ data: [] })
    apiMocks.logicApi.datapointUsages.mockResolvedValue({ data: [] })
    apiMocks.dpApi.writeValue.mockResolvedValue({})
  })

  it('allows writing an internal datapoint without writable adapter bindings', async () => {
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).not.toContain('Kein schreibbares Binding vorhanden.')

    const input = wrapper.find('input[type="text"]')
    await input.setValue('21.5')

    const writeButton = wrapper.findAll('button').find(button => button.text() === 'Schreiben')
    expect(writeButton).toBeTruthy()
    await writeButton.trigger('click')
    await flushPromises()

    expect(apiMocks.dpApi.writeValue).toHaveBeenCalledWith('dp-internal', 21.5)
  })
})
