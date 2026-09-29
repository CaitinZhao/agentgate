<script setup lang="ts">
// Runs page (W6): queue + history merged with the old report center — per-run report access,
// compare selection (>=2 finished runs), big primary launch button, GitHub-style bank/user
// filter combos.
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import Sel from "../components/Sel.vue";
import FilterCombo from "../components/FilterCombo.vue";
import { api, fmtTime, hasMin, store } from "../store";

const { t, locale } = useI18n();
const router = useRouter();
const rows = ref<any[]>([]);
const err = ref("");
const fStatus = ref("");
const fName = ref("");
const fBank = ref("");
const fUser = ref("");
const picked = ref<Record<string, boolean>>({});
const bankOptions = ref<string[]>([]);
const userOptions = ref<string[]>([]);
let timer: any = null;

const statusOptions = [
  { value: "", label: t("runs.filterAll") },
  { value: "queued", label: t("status.queued") },
  { value: "running", label: t("status.running") },
  { value: "succeeded", label: t("status.succeeded") },
  { value: "failed", label: t("status.failed") },
  { value: "cancelled", label: t("status.cancelled") },
];

async function load() {
  try {
    const q = new URLSearchParams();
    if (fStatus.value) q.set("status", fStatus.value);
    if (fName.value) q.set("name", fName.value);
    if (fBank.value) q.set("bank", fBank.value);
    if (hasMin(store.user?.role, "admin") && fUser.value) q.set("user_name", fUser.value);
    rows.value = (await api("GET", "/runs?" + q.toString())).runs;
    // suggestion pools for the combobox filters
    const banks = await api("GET", "/benchmarks");
    bankOptions.value = banks.benchmarks.map((b: any) => b.name);
    if (hasMin(store.user?.role, "admin")) {
      userOptions.value = (await api("GET", "/admin/users")).users.map((u: any) => u.username);
    }
  } catch (e: any) { err.value = e.message; }
}
onMounted(() => { load(); timer = setInterval(load, 3000); });
onUnmounted(() => clearInterval(timer));

async function cancelRun(id: string) {
  try {
    await api("DELETE", "/runs/" + id);
    await load();
  } catch (e: any) { err.value = e.message; }
}
function bankNames(r: any) {
  const names = (r.bank_filter || []).map((x: any) => x.bank);
  if (names.length <= 2) return names.join(", ");
  const more = locale.value === "en" ? ` +${names.length - 2} more` : ` 等${names.length}个`;
  return names.slice(0, 2).join(", ") + more;
}
const pickedIds = computed(() => Object.keys(picked.value).filter((k) => picked.value[k]));
function compare() {
  if (pickedIds.value.length < 2) { err.value = t("reports.pickMore"); return; }
  err.value = "";
  router.push({ path: "/compare", query: { ids: pickedIds.value.join(",") } });
}
</script>

<template>
  <div class="inline" style="margin-bottom: 10px">
    <h2 class="page-title" style="margin: 0">{{ $t("runs.title") }}</h2>
    <span class="spacer" style="flex:1"></span>
    <router-link class="btn primary launch-btn" to="/runs/new">{{ $t("runCreate.title") }}</router-link>
  </div>
  <div class="card">
    <div class="checkbar">
      <Sel :model-value="fStatus" :options="statusOptions" small
           @update:model-value="(v: string) => { fStatus = v; load(); }" />
      <input v-model="fName" :placeholder="$t('runs.filterName')" style="padding:6px;width:160px" @keyup.enter="load" />
      <FilterCombo v-model="fBank" :suggestions="bankOptions" :placeholder="$t('runs.filterBank')" />
      <FilterCombo v-if="hasMin(store.user?.role, 'admin')" v-model="fUser"
                   :suggestions="userOptions" :placeholder="$t('runs.filterUser')" />
      <button class="btn small" @click="load">{{ $t("common.search") }}</button>
      <span class="spacer" style="flex:1"></span>
      <button class="btn" :disabled="pickedIds.length < 2" @click="compare">{{ $t("reports.compare") }}</button>
    </div>
  </div>
  <div v-if="err" class="error-box">{{ err }}</div>
  <div v-if="!rows.length && !err" class="card muted">{{ $t("runs.empty") }}</div>
  <div class="tbl-wrap" v-if="rows.length">
  <table class="tbl">
    <thead><tr>
      <th style="width:30px" :title="$t('reports.hint', { days: 30 })"></th>
      <th>{{ $t("runs.colTime") }}</th><th>{{ $t("runs.colTask") }}</th>
      <th>{{ $t("runs.colUser") }}</th><th>{{ $t("runs.colBanks") }}</th>
      <th>{{ $t("runs.colProgress") }}</th><th :title="$t('runs.colScoreTip')">{{ $t("runs.colScore") }}</th>
      <th>{{ $t("runs.colStatus") }}</th><th>{{ $t("common.actions") }}</th>
    </tr></thead>
    <tbody>
      <tr v-for="r in rows" :key="r.id">
        <td><input v-if="r.result_dir" type="checkbox" v-model="picked[r.id]"
                   :title="$t('reports.pickHint')" /></td>
        <td class="mono">{{ fmtTime(r.created_at) }}</td>
        <td>{{ r.name }}</td>
        <td>{{ r.creator || r.user_name }}</td>
        <td class="muted">{{ bankNames(r) }}</td>
        <td style="min-width: 120px">
          <div class="progressbar"><div :style="{ width: (r.total_cases ? (r.done_cases / r.total_cases) * 100 : 0) + '%' }"></div></div>
          <span class="muted">{{ r.done_cases }}/{{ r.total_cases }}</span>
        </td>
        <td class="mono"><b>{{ r.score || "-" }}</b></td>
        <td><span class="chip" :class="r.status">{{ $t("status." + r.status) }}</span></td>
        <td style="white-space: nowrap">
          <router-link class="btn small" :to="'/runs/' + r.id">{{ $t("runs.detail") }}</router-link>
          <button v-if="r.status === 'queued' || r.status === 'running'" class="btn small danger"
                  @click="cancelRun(r.id)">{{ $t("runs.cancel") }}</button>
        </td>
      </tr>
    </tbody>
  </table>
  </div>
  <div class="muted" style="margin-top: 6px" v-if="rows.some(r => r.status === 'running' || r.status === 'queued')">
    {{ $t("runs.runningHint") }}
  </div>
</template>

<style scoped>
.launch-btn { font-size: 15px; padding: 9px 20px; font-weight: 700; }
</style>
