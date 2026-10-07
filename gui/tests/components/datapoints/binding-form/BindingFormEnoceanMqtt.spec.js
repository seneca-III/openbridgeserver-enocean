import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import BindingFormEnoceanMqtt from '@/components/datapoints/binding-form/BindingFormEnoceanMqtt.vue'

const translations = {
  'adapters.bindingForm.enoceanRepresentationValue': 'Value',
  'adapters.bindingForm.enoceanRepresentationMeaning': 'Meaning',
  'adapters.bindingForm.enoceanSemanticTrue': 'semantic polarity: true',
  'adapters.bindingForm.enoceanSemanticFalse': 'semantic polarity: false',
}

const enumDatapoint = {
  id: 'front_door.lock_contact',
  name: 'lock_contact',
  data_type: 'INTEGER',
  readable: true,
  writable: false,
  runtime_value: { value: 1, meaning: 'door_unlocked' },
  representations: [
    { field: 'value', data_type: 'INTEGER', readable: true, writable: false },
    { field: 'meaning', data_type: 'STRING', readable: true, writable: false },
  ],
  enum: [
    { value: 0, label: 'door_locked', semantic_value: true },
    { value: 1, label: 'door_unlocked', semantic_value: false },
  ],
}

function mountForm({ datapoint = enumDatapoint, direction = 'SOURCE', representation = 'value' } = {}) {
  return mount(BindingFormEnoceanMqtt, {
    props: {
      cfg: { device_id: 'front_door', datapoint_id: datapoint.id, representation },
      direction,
      selectedInstanceId: 'enocean-1',
      enoceanDevices: [{ id: 'front_door', device_name: 'Front door' }],
      enoceanDevicesLoading: false,
      enoceanDevicesError: null,
      enoceanDatapoints: [datapoint],
      enoceanDatapointsLoading: false,
      enoceanDatapointsError: null,
    },
    global: {
      mocks: { $t: key => translations[key] || key },
    },
  })
}

describe('BindingFormEnoceanMqtt enum representations', () => {
  it('offers numeric value and string meaning for a readable enum', () => {
    const options = mountForm().get('[data-testid="enocean-representation"]').findAll('option')

    expect(options.map(option => option.attributes('value'))).toEqual(['value', 'meaning'])
    expect(options.map(option => option.text())).toEqual(['Value (INTEGER)', 'Meaning (STRING)'])
  })

  it('does not offer meaning to a writable binding', () => {
    const datapoint = {
      ...enumDatapoint,
      writable: true,
      representations: [
        { field: 'value', data_type: 'INTEGER', readable: true, writable: true },
        { field: 'meaning', data_type: 'STRING', readable: true, writable: false },
      ],
    }
    const options = mountForm({ datapoint, direction: 'DEST' })
      .get('[data-testid="enocean-representation"]')
      .findAll('option')

    expect(options.map(option => option.attributes('value'))).toEqual(['value'])
  })

  it('shows semantic polarity exactly as advertised by enum metadata', () => {
    const text = mountForm().text()

    expect(text).toContain('0 = door_locked')
    expect(text).toContain('semantic polarity: true')
    expect(text).toContain('1 = door_unlocked')
    expect(text).toContain('semantic polarity: false')
  })

  it('does not fabricate a meaning representation for a float measurement', () => {
    const datapoint = {
      id: 'meter.voltage',
      name: 'voltage',
      data_type: 'FLOAT',
      unit: 'V',
      readable: true,
      writable: false,
      representations: [
        { field: 'value', data_type: 'FLOAT', readable: true, writable: false },
      ],
    }
    const options = mountForm({ datapoint }).get('[data-testid="enocean-representation"]').findAll('option')

    expect(options.map(option => option.text())).toEqual(['Value (FLOAT)'])
  })

  it('keeps legacy discovery without a representation selector', () => {
    const datapoint = {
      id: 'door.state',
      name: 'state',
      data_type: 'STRING',
      readable: true,
      writable: false,
    }

    expect(mountForm({ datapoint }).find('[data-testid="enocean-representation"]').exists()).toBe(false)
  })
})
