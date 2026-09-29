<script setup lang="ts">
// Run comparison (doc 35 C6): six-dimension radar overlay + per-dimension Δ table,
// plus the case-level transfer table from control/compare.
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute } from "vue-router";
import { marked } from "marked";
import { api } from "../store";
import ScoreRadar from "../components/ScoreRadar.vue";

const { t, locale } = useI18n();
const route = useRoute();
const ids = computed(() => (route.query.ids as string || "").split(",").filter(Boolean));
const result = ref<any>(null);
const scoreA = ref<any>(null);
const scoreB = ref<any>(null);
const err = ref("");
const dims = ["success", "quality", "reliability", "stability", "efficiency", "cost", "safety"];

function md(src: string): string {
  const html = marked.parse(src || "", { async: false }) as string;
  return html.replace(/<script[\s\S]*?<\/script>/gi, "");
}

function deltaColor(d: number | null): string {
  if (d === null || d === 0) return "color:#64748b";      // no change -> neutral
  return d < 0 ? "color:#dc2626" : "color:#059669";
}
function delta(a: number | null, b: number | null): number | null {
  if (typeof a !== "number" || typeof b !== "number") return null;
  return Math.round(a - b);
}

onMounted(async () => {
  if (ids.value.length < 2) { err.value = t("compare.needTwo"); return; }
  try {
    result.value = await api("POST", "/compare", { run_ids: ids.value, lang: locale.value });
  } catch (e: any) { err.value = e.message; }
  // scores for the radar overlay (missing files / legacy runs degrade gracefully)
  for (const [i, rid] of ids.value.slice(0, 2).entries()) {
    try {
      const s = await api("GET", `/runs/${rid}/scores`);
      if (i === 0) scoreA.value = s; else scoreB.value = s;
    } catch { /* pre-C1 run */ }
  }
});
</script>

<template>
  <div class="inline" style="margin-bottom: 8px">
    <router-link class="btn small" to="/runs">{{ $t("compare.back") }}</router-link>
    <h2 class="page-title" style="margin: 0">{{ $t("compare.title") }}</h2>
    <span class="mono muted">{{ ids.join(" vs ") }}</span>
  </div>
  <div v-if="err" class="error-box">{{ err }}</div>

  <div class="card" v-if="scoreA || scoreB">
    <h4>{{ $t("compare.sixDim") }}</h4>
    <div class="inline" style="align-items: flex-start; flex-wrap: wrap">
      <ScoreRadar :scores="scoreA?.run_scores || {}" :compare="scoreB?.run_scores || null" :size="280" />
      <div style="flex: 1; min-width: 280px">
        <table class="tbl">
          <thead><tr>
            <th>{{ $t("compare.dimension") }}</th>
            <th class="mono">{{ ids[0] }}</th>
            <th class="mono">{{ ids[1] }}</th>
            <th>Δ</th>
          </tr></thead>
          <tbody>
            <tr v-for="d in dims" :key="d">
              <td>{{ $t("scores.dim." + d) }}</td>
              <td>{{ scoreA?.run_scores?.[d] ?? "n/a" }}</td>
              <td>{{ scoreB?.run_scores?.[d] ?? "n/a" }}</td>
              <td><b :style="deltaColor(delta(scoreA?.run_scores?.[d], scoreB?.run_scores?.[d]))">
                {{ delta(scoreA?.run_scores?.[d], scoreB?.run_scores?.[d]) ?? "–" }}</b></td>
            </tr>
            <tr>
              <td><b>{{ $t("scores.total") }}</b></td>
              <td><b>{{ scoreA?.total ?? "n/a" }}</b></td>
              <td><b>{{ scoreB?.total ?? "n/a" }}</b></td>
              <td><b :style="deltaColor(delta(scoreA?.total, scoreB?.total))">{{ delta(scoreA?.total, scoreB?.total) ?? "–" }}</b></td>
            </tr>
          </tbody>
        </table>
        <div class="muted" style="margin-top: 6px">{{ $t("compare.legend") }}</div>
      </div>
    </div>
  </div>

  <template v-if="result">
    <div class="card muted">{{ $t("compare.transferHint") }}</div>
    <div class="card md-body" v-html="md(locale === 'en' ? result.report_en : result.report_zh)"></div>
  </template>
</template>
