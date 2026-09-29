<script setup lang="ts">
// Custom in-page dropdown: the option list is rendered by the page itself (Teleport+fixed,
// anchored right under the control) instead of the OS-level native select popup, so it
// always looks attached, survives overflow containers, and is styleable.
import { nextTick, onBeforeUnmount, onMounted, ref } from "vue";

const props = withDefaults(defineProps<{
  modelValue: string;
  options: { value: string; label: string }[];
  small?: boolean;        // compact variant for table rows
  placeholder?: string;   // shown when modelValue is "" or unmatched
  minWidth?: string;
}>(), { small: false, placeholder: "", minWidth: "" });

const emit = defineEmits<{ (e: "update:modelValue", v: string): void }>();

const open = ref(false);
const btn = ref<HTMLElement | null>(null);
const pop = ref<HTMLElement | null>(null);
const pos = ref({ top: 0, left: 0, minWidth: 0 });

function currentLabel(): string {
  const hit = props.options.find((o) => o.value === props.modelValue);
  return hit ? hit.label : (props.placeholder || props.modelValue || "-");
}

function place() {
  if (!btn.value) return;
  const r = btn.value.getBoundingClientRect();
  pos.value = { top: r.bottom + 4, left: r.left, minWidth: r.width };
  nextTick(() => {
    const ph = pop.value ? pop.value.getBoundingClientRect().height : 0;
    if (r.bottom + ph + 8 > window.innerHeight) {
      pos.value = { ...pos.value, top: Math.max(8, r.top - ph - 4) };  // flip above
    }
  });
}

function toggle() {
  open.value = !open.value;
  if (open.value) place();
}

function pick(v: string) {
  emit("update:modelValue", v);
  open.value = false;
}

function onDocClick(e: MouseEvent) {
  if (!open.value) return;
  const t = e.target as Node;
  if (btn.value?.contains(t) || pop.value?.contains(t)) return;
  open.value = false;
}

function onKey(e: KeyboardEvent) {
  if (e.key === "Escape") open.value = false;
}

function onReposition() {
  if (open.value) place();
}

onMounted(() => {
  document.addEventListener("click", onDocClick, true);
  document.addEventListener("keydown", onKey);
  window.addEventListener("scroll", onReposition, true);
  window.addEventListener("resize", onReposition);
});
onBeforeUnmount(() => {
  document.removeEventListener("click", onDocClick, true);
  document.removeEventListener("keydown", onKey);
  window.removeEventListener("scroll", onReposition, true);
  window.removeEventListener("resize", onReposition);
});
</script>

<template>
  <button ref="btn" type="button" class="sel" :class="{ small, open }" @click="toggle">
    <span class="sel-label">{{ currentLabel() }}</span>
    <span class="sel-arrow">▾</span>
  </button>
  <Teleport to="body">
    <div v-if="open" ref="pop" class="sel-pop"
         :style="{ top: pos.top + 'px', left: pos.left + 'px',
                   minWidth: (pos.minWidth || 120) + 'px', maxWidth: '320px' }">
      <div v-for="o in options" :key="o.value" class="sel-opt"
           :class="{ active: o.value === modelValue }" @click="pick(o.value)">
        {{ o.label }}
      </div>
    </div>
  </Teleport>
</template>
