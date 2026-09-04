<template>
  <div class="section-header">{{ $t('adapters.bindingForm.enoceanSection') }}</div>

  <div class="form-group">
    <label class="label">{{ $t('adapters.bindingForm.enoceanDeviceLabel') }}</label>
    <div class="flex gap-2">
      <select
        :value="cfg.device_id"
        class="input flex-1"
        :disabled="enoceanDevicesLoading"
        @change="$emit('select-enocean-device', $event.target.value)"
      >
        <option value="">{{ $t('adapters.bindingForm.enoceanDevicePlaceholder') }}</option>
        <option v-for="device in enoceanDevices" :key="device.id" :value="device.id">
          {{ displayDeviceName(device) }}
        </option>
      </select>
      <button
        type="button"
        class="btn-secondary px-3 text-sm whitespace-nowrap"
        :disabled="!selectedInstanceId || enoceanDevicesLoading"
        @click="$emit('browse-enocean-devices')"
      >
        <span v-if="enoceanDevicesLoading" class="inline-block w-3 h-3 border-2 border-current border-t-transparent rounded-full animate-spin mr-1"></span>
        {{ enoceanDevicesLoading ? $t('adapters.bindingForm.loading') : $t('adapters.bindingForm.browse') }}
      </button>
    </div>
    <p class="hint">{{ $t('adapters.bindingForm.enoceanDeviceHint') }}</p>
    <p v-if="enoceanDevicesError" class="text-xs text-red-400 mt-1">{{ enoceanDevicesError }}</p>
  </div>

  <div v-if="selectedDevice" class="rounded-lg border border-slate-200 dark:border-slate-700 bg-slate-50 dark:bg-slate-800/40 px-3 py-2 text-xs text-slate-500">
    <div class="flex items-center gap-2 min-w-0">
      <span class="font-medium text-slate-700 dark:text-slate-200 truncate">{{ displayDeviceName(selectedDevice) }}</span>
      <span v-if="selectedDevice.eep" class="px-1.5 py-0.5 rounded bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 shrink-0">{{ selectedDevice.eep }}</span>
      <span v-if="selectedDevice.datapoints_count" class="shrink-0">{{ selectedDevice.datapoints_count }} {{ $t('adapters.bindingForm.enoceanDatapointsCount') }}</span>
    </div>
    <div v-if="selectedDeviceMeta.length" class="mt-1 truncate">
      {{ selectedDeviceMeta.join(' · ') }}
    </div>
  </div>

  <div class="form-group">
    <label class="label">{{ $t('adapters.bindingForm.enoceanDatapointLabel') }}</label>
    <div class="flex gap-2">
      <select
        v-model="cfg.datapoint_id"
        class="input flex-1"
        :disabled="!cfg.device_id || enoceanDatapointsLoading"
        required
        @change="$emit('select-enocean-datapoint', cfg.datapoint_id)"
      >
        <option value="">{{ $t('adapters.bindingForm.enoceanDatapointPlaceholder') }}</option>
        <option v-for="dp in enoceanDatapoints" :key="dp.id" :value="dp.id">
          {{ dp.name || dp.id }}
        </option>
      </select>
      <button
        type="button"
        class="btn-secondary px-3 text-sm whitespace-nowrap"
        :disabled="!selectedInstanceId || !cfg.device_id || enoceanDatapointsLoading"
        @click="$emit('browse-enocean-datapoints')"
      >
        <span v-if="enoceanDatapointsLoading" class="inline-block w-3 h-3 border-2 border-current border-t-transparent rounded-full animate-spin mr-1"></span>
        {{ enoceanDatapointsLoading ? $t('adapters.bindingForm.loading') : $t('adapters.bindingForm.browse') }}
      </button>
    </div>
    <p class="hint">{{ $t('adapters.bindingForm.enoceanDatapointHint') }}</p>

    <div
      v-if="enoceanDatapoints.length > 0"
      class="mt-2 max-h-64 overflow-y-auto border border-slate-200 dark:border-slate-700 rounded-lg divide-y divide-slate-100 dark:divide-slate-700/50 bg-white dark:bg-slate-800"
    >
      <button
        v-for="dp in enoceanDatapoints"
        :key="dp.id"
        type="button"
        class="w-full text-left px-3 py-2 hover:bg-slate-50 dark:hover:bg-slate-700/50"
        @click="$emit('select-enocean-datapoint', dp.id)"
      >
        <div class="flex items-center gap-2 min-w-0">
          <span class="font-mono text-sm text-slate-700 dark:text-slate-100 truncate">{{ dp.name || dp.id }}</span>
          <span class="text-[11px] px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-700 text-slate-500 shrink-0">{{ dp.data_type || 'UNKNOWN' }}</span>
          <span v-if="dp.readable" class="text-[11px] px-1.5 py-0.5 rounded bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 shrink-0">RO</span>
          <span v-if="dp.writable" class="text-[11px] px-1.5 py-0.5 rounded bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-300 shrink-0">WO</span>
          <span v-if="dp.unit" class="text-xs text-slate-500 shrink-0">{{ dp.unit }}</span>
        </div>
        <div v-if="dp.role || (dp.value !== null && dp.value !== undefined)" class="mt-0.5 flex items-center gap-2 text-xs text-slate-500">
          <span v-if="dp.role" class="shrink-0">{{ dp.role }}</span>
          <span v-if="dp.value !== null && dp.value !== undefined" class="font-mono shrink-0">= {{ dp.value }}</span>
        </div>
      </button>
    </div>
    <p v-if="enoceanDatapointsError" class="text-xs text-red-400 mt-1">{{ enoceanDatapointsError }}</p>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  cfg: { type: Object, required: true },
  selectedInstanceId: { type: [String, Number, null], default: null },
  enoceanDevices: { type: Array, required: true },
  enoceanDevicesLoading: { type: Boolean, required: true },
  enoceanDevicesError: { type: [String, null], default: null },
  enoceanDatapoints: { type: Array, required: true },
  enoceanDatapointsLoading: { type: Boolean, required: true },
  enoceanDatapointsError: { type: [String, null], default: null },
})

defineEmits([
  'browse-enocean-devices',
  'browse-enocean-datapoints',
  'select-enocean-device',
  'select-enocean-datapoint',
])

const selectedDevice = computed(() =>
  props.enoceanDevices.find(device => String(device.id) === String(props.cfg.device_id)) ?? null
)

const selectedDeviceMeta = computed(() => {
  if (!selectedDevice.value) return []
  const label = displayDeviceName(selectedDevice.value)
  return uniqueDeviceParts([
    selectedDevice.value.manufacturer,
    selectedDevice.value.alias,
    selectedDevice.value.id,
  ], label)
})

function displayDeviceName(device) {
  return device?.device_name || device?.name || device?.alias || device?.id || ''
}

function uniqueDeviceParts(parts, label) {
  const seen = new Set([String(label || '').trim()])
  const result = []
  for (const part of parts) {
    const text = String(part || '').trim()
    if (!text || seen.has(text)) continue
    seen.add(text)
    result.push(text)
  }
  return result
}
</script>
