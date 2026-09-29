<script setup lang="ts">
// Six-dimension radar (doc 35 §2): SVG, no deps. Draws one or two polygons (compare mode).
// Dimensions without data (null) show "n/a" and BREAK the polygon at that axis — no fake
// zero, no misleading chord across the missing dimension. Labels use the same names as the
// diagnostic chips (scores.dim.*) and the canvas is wide enough to avoid label clipping.
import { computed } from "vue";
import { useI18n } from "vue-i18n";

const props = withDefaults(defineProps<{
  scores: Record<string, number | null>;
  compare?: Record<string, number | null> | null;
  size?: number;
}>(), { compare: null, size: 260 });

const DIMS = ["success", "quality", "reliability", "stability", "efficiency", "cost", "safety"];
const { t } = useI18n();

const PAD = 150;                       // horizontal room for axis labels on both sides
const cx = props.size / 2 + PAD / 2;
const cy = props.size / 2 + 6;
const R = props.size / 2 - 40;

function pt(i: number, r: number): [number, number] {
  const angle = -Math.PI / 2 + (2 * Math.PI * i) / DIMS.length;
  return [cx + r * Math.cos(angle), cy + r * Math.sin(angle)];
}
function val(scores: Record<string, number | null> | null | undefined, i: number): number | null {
  const v = scores?.[DIMS[i]];
  return typeof v === "number" ? Math.max(0, Math.min(100, v)) : null;
}
/** line segments between consecutive measurable axes; the missing axis breaks the shape */
function segments(scores: Record<string, number | null> | null | undefined, r0 = R) {
  const segs: string[] = [];
  for (let i = 0; i < DIMS.length; i++) {
    const j = (i + 1) % DIMS.length;
    const a = val(scores, i);
    const b = val(scores, j);
    if (a === null || b === null) continue;
    const [x1, y1] = pt(i, (r0 * a) / 100);
    const [x2, y2] = pt(j, (r0 * b) / 100);
    segs.push(`M${x1.toFixed(1)},${y1.toFixed(1)} L${x2.toFixed(1)},${y2.toFixed(1)}`);
  }
  return segs.join(" ");
}
const segsA = computed(() => segments(props.scores));
const segsB = computed(() => (props.compare ? segments(props.compare) : ""));
/** fill only when the polygon is complete (no broken axis) — open paths must not fill */
const completeA = computed(() => DIMS.every((_, i) => val(props.scores, i) !== null));
const completeB = computed(() => props.compare
  ? DIMS.every((_, i) => val(props.compare, i) !== null) : false);
const rings = [25, 50, 75, 100];
function labelPos(i: number): { x: number; y: number; anchor: string } {
  const [x, y] = pt(i, R + 18);
  const anchor = x > cx + 4 ? "start" : (x < cx - 4 ? "end" : "middle");
  return { x, y: y + 4, anchor };
}
const labels = computed(() => DIMS.map((d, i) => {
  const { x, y, anchor } = labelPos(i);
  const v = val(props.scores, i);
  const cmp = val(props.compare, i);
  const name = t("scores.dim." + d);
  const fmt = (n: number | null) => (n === null ? "n/a" : String(Math.round(n)));
  return { x, y, anchor, name,
           text: `${name} ${fmt(v)}${props.compare ? " / " + fmt(cmp) : ""}` };
}));
</script>

<template>
  <svg :width="size + PAD" :height="size" class="radar">
    <g v-for="r in rings" :key="r">
      <polygon :points="DIMS.map((_, i) => pt(i, (R * r) / 100).map((v) => v.toFixed(1)).join(',')).join(' ')"
               fill="none" stroke="#e2e8f0" stroke-width="1" />
    </g>
    <line v-for="(d, i) in DIMS" :key="'spoke' + i" :x1="cx" :y1="cy"
          :x2="pt(i, R)[0].toFixed(1)" :y2="pt(i, R)[1].toFixed(1)" stroke="#e2e8f0" />
    <path v-if="segsB" :d="segsB" fill="rgba(59,130,246,.10)" :fill-opacity="completeB ? 1 : 0"
          stroke="#3b82f6" stroke-width="1.5" stroke-dasharray="4 3" />
    <path v-if="segsA" :d="segsA" fill="rgba(16,185,129,.16)" :fill-opacity="completeA ? 1 : 0"
          stroke="#059669" stroke-width="2" />
    <text v-for="(l, i) in labels" :key="'lb' + i" :x="l.x" :y="l.y" :text-anchor="l.anchor"
          class="radar-label" font-size="11.5" fill="#475569">{{ l.text }}</text>
  </svg>
</template>

<style>
.radar .radar-label { font-family: inherit; }
</style>
