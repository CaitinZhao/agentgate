<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute } from "vue-router";
import Sel from "../components/Sel.vue";
import { api, fmtTime } from "../store";

const { t } = useI18n();
const route = useRoute();
const name = route.params.name as string;
const info = ref<any>(null);
const runs = ref<any[]>([]);
const err = ref("");

// filters (W6-4): creator / task name / outcome; everyone sees everyone's runs
const fCreator = ref("");
const fName = ref("");
const fOutcome = ref("");
const outcomeOptions = computed(() => [
  { value: "", label: t("overview.outcomeAll") },
  { value: "ok", label: t("overview.outcomeOk") },
  { value: "fail", label: t("overview.outcomeFail") },
  { value: "pending", label: t("overview.outcomePending") },
]);
const creatorOptions = computed(() => {
  const set = Array.from(new Set(runs.value.map((r) => r.creator || r.user_name).filter(Boolean)));
  return [{ value: "", label: t("overview.creatorAll") },
          ...set.map((c) => ({ value: c, label: c }))];
});
const filtered = computed(() => runs.value.filter((r) => {
  const creator = r.creator || r.user_name || "";
  if (fCreator.value && creator !== fCreator.value) return false;
  if (fName.value && !r.name.includes(fName.value)) return false;
  if (fOutcome.value === "ok" && !(r.status === "succeeded" && r.gate_decision === "GREEN")) return false;
  if (fOutcome.value === "fail" && (r.status !== "succeeded" || (r.gate_decision || "").startsWith("FAIL"))) return false;
  if (fOutcome.value === "pending" && !(r.status === "succeeded" && (r.gate_decision || "").startsWith("PENDING"))) return false;
  return true;
}));

onMounted(async () => {
  try {
    info.value = await api("GET", "/benchmarks/" + name + "/overview");
    runs.value = info.value.runs || [];
  } catch (e: any) { err.value = e.message; }
});
</script>

<template>
  <div class="inline" style="margin-bottom: 8px">
    <router-link class="btn small" :to="'/banks/' + name">{{ $t("overview.backToBank") }}</router-link>
    <h2 class="page-title" style="margin: 0">{{ info?.display_name || name }} · {{ $t("overview.title") }}</h2>
  </div>
  <div v-if="err" class="error-box">{{ err }}</div>
  <template v-if="info">
    <div class="stat-grid">
      <div class="stat"><div class="num">{{ info.case_count }}</div><div class="lbl">{{ $t("overview.caseCount") }}</div></div>
      <div class="stat"><div class="num">{{ info.total_runs }}</div><div class="lbl">{{ $t("overview.totalRuns") }}</div></div>
      <div class="stat"><div class="num">{{ info.runs_10d }}</div><div class="lbl">{{ $t("overview.runs10d") }}</div></div>
      <div class="stat"><div class="num">{{ info.success_rate_10d === null ? "-" : (info.success_rate_10d * 100).toFixed(0) + "%" }}</div><div class="lbl">{{ $t("overview.successRate") }}</div></div>
      <div class="stat"><div class="num">{{ info.recent_failures }}</div><div class="lbl">{{ $t("overview.recentFailures") }}</div></div>
    </div>

    <h3 class="section-title">{{ $t("overview.runs") }}</h3>
    <div class="card">
      <div class="checkbar">
        <span class="muted">{{ $t("overview.colUser") }}:</span>
        <Sel :model-value="fCreator" :options="creatorOptions" small
             @update:model-value="(v: string) => (fCreator = v)" />
        <input v-model="fName" :placeholder="$t('runs.filterName')" style="padding:5px;width:150px" />
        <Sel :model-value="fOutcome" :options="outcomeOptions" small
             @update:model-value="(v: string) => (fOutcome = v)" />
      </div>
    </div>
    <div v-if="!filtered.length" class="card muted">{{ $t("overview.empty") }}</div>
    <div class="tbl-wrap" v-if="filtered.length">
    <table class="tbl">
      <thead><tr>
        <th>{{ $t("overview.colTime") }}</th><th>{{ $t("overview.colTask") }}</th>
        <th>{{ $t("overview.colUser") }}</th><th>{{ $t("overview.colGate") }}</th>
        <th>{{ $t("overview.colScore") }}</th><th>{{ $t("overview.colStatus") }}</th>
        <th></th>
      </tr></thead>
      <tbody>
        <tr v-for="r in filtered" :key="r.id">
          <td class="mono">{{ fmtTime(r.created_at) }}</td>
          <td>{{ r.name }}</td>
          <td>{{ r.creator || r.user_name }}</td>
          <td><span class="chip" :class="r.status"><span class="mono">{{ r.score || r.gate_decision || "-" }}</span></span></td>
          <td class="mono">{{ r.score || "-" }}</td>
          <td><span class="chip" :class="r.status">{{ $t("status." + r.status) }}</span></td>
          <td><router-link class="btn small" :to="'/runs/' + r.id">{{ $t("runs.detail") }}</router-link></td>
        </tr>
      </tbody>
    </table>
    </div>
  </template>
</template>
