<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import { api, fmtTime } from "../store";

const { t } = useI18n();
const router = useRouter();
const rows = ref<any[]>([]);
const picked = ref<Record<string, boolean>>({});
const err = ref("");
const days = ref(30);

onMounted(async () => {
  try {
    rows.value = (await api("GET", "/runs?limit=200")).runs
      .filter((r: any) => r.result_dir);
  } catch (e: any) { err.value = e.message; }
});

const pickedIds = ref<string[]>([]);
function compare() {
  pickedIds.value = Object.keys(picked.value).filter((k) => picked.value[k]);
  if (pickedIds.value.length < 2) { err.value = t("reports.pickMore"); return; }
  err.value = "";
  router.push({ path: "/compare", query: { ids: pickedIds.value.join(",") } });
}

function bankNames(r: any) {
  return (r.bank_filter || []).map((x: any) => x.bank).join(", ");
}
</script>

<template>
  <h2 class="page-title">{{ $t("reports.title") }}</h2>
  <div class="muted" style="margin-bottom: 8px">{{ $t("reports.hint", { days }) }}</div>
  <div v-if="err" class="error-box">{{ err }}</div>
  <div v-if="!rows.length" class="card muted">{{ $t("reports.empty") }}</div>
  <div class="tbl-wrap">
  <table class="tbl" v-if="rows.length">
    <thead><tr>
      <th style="width:30px"></th>
      <th>{{ $t("reports.colTime") }}</th><th>{{ $t("reports.colTask") }}</th>
      <th>{{ $t("reports.colUser") }}</th><th>{{ $t("reports.colBanks") }}</th>
      <th>{{ $t("reports.colGate") }}</th><th>{{ $t("reports.colScore") }}</th>
      <th>{{ $t("reports.colStatus") }}</th><th></th>
    </tr></thead>
    <tbody>
      <tr v-for="r in rows" :key="r.id">
        <td><input type="checkbox" v-model="picked[r.id]" /></td>
        <td class="mono">{{ fmtTime(r.finished_at || r.created_at) }}</td>
        <td>{{ r.name }}</td>
        <td>{{ r.creator || r.user_name }}</td>
        <td class="muted">{{ bankNames(r) }}</td>
        <td><span class="chip" :class="r.status">{{ r.gate_decision || "-" }}</span></td>
        <td class="mono">{{ r.score || "-" }}</td>
        <td><span class="chip" :class="r.status">{{ $t("status." + r.status) }}</span></td>
        <td><router-link class="btn small" :to="'/runs/' + r.id">{{ $t("runs.detail") }}</router-link></td>
      </tr>
    </tbody>
  </table>
  </div>
  <div style="margin-top: 10px">
    <button class="btn primary" @click="compare">{{ $t("reports.compare") }}</button>
  </div>
</template>
