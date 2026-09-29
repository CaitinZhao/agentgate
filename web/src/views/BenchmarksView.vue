<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import Sel from "../components/Sel.vue";
import { api, hasMin, store } from "../store";

const { t, locale } = useI18n();
const rows = ref<any[]>([]);
const err = ref("");
const category = ref("");
const mine = ref(false);
const kw = ref("");
const onlyFailed = ref(false);            // W6-2: only banks with recent failures
const sortKey = ref("");
const sortDir = ref(1);                   // 1 asc / -1 desc
const SORTABLE = ["name", "category", "case_count", "recent_failures", "runs_10d"];
function sortBy(k: string) {
  if (!SORTABLE.includes(k)) return;
  if (sortKey.value === k) sortDir.value = -sortDir.value;
  else { sortKey.value = k; sortDir.value = 1; }
}
function sortMark(k: string) {
  return sortKey.value === k ? (sortDir.value === 1 ? " ↑" : " ↓") : "";
}
const categoryOptions = computed(() => [
  { value: "", label: t("banks.filterAll") },
  ...Array.from(new Set(rows.value.map((b) => b.category).filter(Boolean)))
    .map((c) => ({ value: c as string, label: c as string })),
]);

function localeDesc(b: any) {
  return locale.value === "en" ? b.description_en : b.description_zh;
}

async function load() {
  err.value = "";
  try {
    const r = await api("GET", "/benchmarks");
    rows.value = r.benchmarks;
  } catch (e: any) { err.value = e.message; }
}
onMounted(load);

const visible = computed(() => {
  let out = rows.value.filter((b) =>
    (!category.value || b.category === category.value) &&
    (!mine.value || b.owner_name === store.user?.username) &&
    (!kw.value || b.name.includes(kw.value) || (b.display_name || "").includes(kw.value)) &&
    (!onlyFailed.value || b.recent_failures > 0));
  if (sortKey.value) {
    out = [...out].sort((a, b) => {
      const va = a[sortKey.value] ?? "";
      const vb = b[sortKey.value] ?? "";
      return (typeof va === "number" && typeof vb === "number"
        ? va - vb : String(va).localeCompare(String(vb))) * sortDir.value;
    });
  }
  return out;
});

function distStyle(b: any) {
  const total = b.case_count || 1;
  const d = b.level_dist || {};
  return {
    l0: ((d.L0 || 0) / total) * 100,
    l1: ((d.L1 || 0) / total) * 100,
    l2: ((d.L2 || 0) / total) * 100,
  };
}
</script>

<template>
  <h2 class="page-title">{{ $t("banks.title") }}</h2>
  <div class="card">
    <div class="checkbar">
      <span class="muted">{{ $t("banks.category") }}:</span>
      <Sel v-model="category" :options="categoryOptions" />
      <label><input type="checkbox" v-model="mine" /> {{ $t("banks.mine") }}</label>
      <label><input type="checkbox" v-model="onlyFailed" /> {{ $t("banks.recentFailed") }}</label>
      <input v-model="kw" :placeholder="$t('common.search')" style="padding:5px;width:160px" />
      <span class="spacer" style="flex:1"></span>
      <router-link v-if="hasMin(store.user?.role, 'member')" to="/banks/new" class="btn primary">
        {{ $t("banks.newBank") }}</router-link>
    </div>
  </div>
  <div v-if="err" class="error-box">{{ err }}</div>
  <div v-if="!visible.length && !err" class="card muted">{{ $t("banks.none") }}</div>

  <div class="tbl-wrap">
  <table class="tbl" v-if="visible.length">
    <thead><tr>
      <th class="sortable" @click="sortBy('name')">{{ $t("banks.title") }}{{ sortMark("name") }}</th>
      <th class="sortable" @click="sortBy('category')">{{ $t("banks.category") }}{{ sortMark("category") }}</th>
      <th>{{ $t("banks.visibility") }}</th>
      <th class="sortable" @click="sortBy('case_count')">{{ $t("banks.cases") }}{{ sortMark("case_count") }}</th>
      <th>{{ $t("banks.levelDist") }}</th>
      <th class="sortable" @click="sortBy('runs_10d')">{{ $t("banks.runs10d") }}{{ sortMark("runs_10d") }}</th>
      <th class="sortable" @click="sortBy('recent_failures')">{{ $t("banks.failures") }}{{ sortMark("recent_failures") }}</th>
      <th>{{ $t("banks.lastRun") }}</th>
      <th>{{ $t("common.actions") }}</th>
    </tr></thead>
    <tbody>
      <tr v-for="b in visible" :key="b.name">
        <td>
          <b>{{ b.display_name || b.name }}</b>
          <div v-if="(b.display_name || b.name) !== b.name" class="mono muted">{{ b.name }}<span v-if="b.status === 'offline'"> · {{ $t("banks.offlineHint") }}</span></div>
          <div v-else-if="b.status === 'offline'" class="muted">{{ $t("banks.offlineHint") }}</div>
          <div class="muted">{{ localeDesc(b) }}</div>
        </td>
        <td>{{ b.category || $t("banks.uncategorized") }}</td>
        <td><span class="chip" :class="b.visibility">{{ $t("banks." + b.visibility) }}</span></td>
        <td>{{ b.case_count }}</td>
        <td>
          <div class="distbar">
            <div class="l0" :style="{ width: distStyle(b).l0 + '%' }"></div>
            <div class="l1" :style="{ width: distStyle(b).l1 + '%' }"></div>
            <div class="l2" :style="{ width: distStyle(b).l2 + '%' }"></div>
          </div>
          <span class="muted">{{ b.level_dist?.L0 || 0 }}/{{ b.level_dist?.L1 || 0 }}/{{ b.level_dist?.L2 || 0 }}</span>
        </td>
        <td>{{ b.runs_10d }}<span class="muted"> / {{ b.total_runs }}</span></td>
        <td>{{ b.recent_failures }}</td>
        <td>
          <template v-if="b.last_run">
            <span class="chip" :class="b.last_run.status">{{ b.last_run.gate_decision || b.last_run.status }}</span>
            <div class="muted mono">{{ b.last_run.created_at }}</div>
          </template>
          <span v-else class="muted">{{ $t("common.none") }}</span>
        </td>
        <td style="white-space: nowrap">
          <router-link class="btn small" :to="'/banks/' + b.name">{{ $t("banks.detail") }}</router-link>
          <router-link class="btn small" :to="'/banks/' + b.name + '/overview'">{{ $t("banks.overview") }}</router-link>
        </td>
      </tr>
    </tbody>
  </table>
  </div>
</template>
