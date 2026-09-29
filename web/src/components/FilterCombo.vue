<script setup lang="ts">
// GitHub-issue-style filter combobox: free-text fuzzy matching with a suggestion dropdown.
import { computed, ref } from "vue";

const props = defineProps<{
  modelValue: string;
  suggestions: string[];
  placeholder?: string;
  width?: string;
}>();
const emit = defineEmits<{ (e: "update:modelValue", v: string): void }>();

const focused = ref(false);
const input = ref<HTMLInputElement | null>(null);

const matches = computed(() => {
  const q = props.modelValue.trim().toLowerCase();
  const list = props.suggestions
    .filter((s) => !q || s.toLowerCase().includes(q))
    .slice(0, 8);
  return list;
});

function pick(v: string) {
  emit("update:modelValue", v === props.modelValue ? "" : v);  // re-pick clears
  focused.value = false;
}
function clear() {
  emit("update:modelValue", "");
}
</script>

<template>
  <span class="fcombo" :style="{ width: width || '170px' }">
    <input ref="input" :value="modelValue" :placeholder="placeholder"
           @input="emit('update:modelValue', ($event.target as HTMLInputElement).value)"
           @focus="focused = true" @blur="focused = false" />
    <span v-if="modelValue" class="fcombo-x" @mousedown.prevent="clear()">×</span>
    <div v-if="focused && matches.length" class="sel-pop" style="position:absolute; top:100%; left:0; margin-top:4px">
      <div v-for="s in matches" :key="s" class="sel-opt"
           :class="{ active: s === modelValue }" @mousedown.prevent="pick(s)">{{ s }}</div>
    </div>
  </span>
</template>

<style scoped>
.fcombo { position: relative; display: inline-block; }
.fcombo input {
  width: 100%; border: 1px solid #cbd5e0; border-radius: 6px; padding: 6px 22px 6px 8px;
  font-size: 13px; font-family: inherit; background: #fff; color: #1f2933;
}
.fcombo input:focus { outline: 2px solid #93c5fd; outline-offset: 1px; }
.fcombo-x {
  position: absolute; right: 6px; top: 50%; transform: translateY(-50%);
  color: #627d98; cursor: pointer; font-size: 14px; line-height: 1;
}
</style>
