<script setup lang="ts">
// Bank detail page (W6 split): browse cases + per-user "my configuration" on public banks.
// Case content editing (add/edit/delete/batch) lives in the separate BankEditView.
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import Sel from "../components/Sel.vue";
import { api, download, guideUrl, hasMin, store } from "../store";

const { t } = useI18n();
const router = useRouter();
const props = defineProps<{ name: string }>();
const bank = ref<any>(null);
const cases = ref<any[]>([]);
const err = ref("");
const msg = ref("");

const canManage = computed(() => {
  if (!bank.value || !store.user) return false;
  if (bank.value.visibility === "public") return hasMin(store.user.role, "admin");
  return bank.value.owner_name === store.user.username || hasMin(store.user.role, "admin");
});
const isPublic = computed(() => bank.value?.visibility === "public");
const LEVELS = computed(() => [
  { value: "L0", label: "L0" }, { value: "L1", label: "L1" }, { value: "L2", label: "L2" }]);
const STRICT = computed(() => [
  { value: "yes", label: t("detail.toolStrictOn") },
  { value: "no", label: t("detail.toolStrictOff") }]);
const MY_LEVELS = computed(() => [
  { value: "", label: t("detail.followDefault") },
  { value: "L0", label: "L0" }, { value: "L1", label: "L1" }, { value: "L2", label: "L2" }]);

// ---- pagination ----
const page = ref(1);
const pageSize = 50;
const pages = computed(() => Math.max(1, Math.ceil(cases.value.length / pageSize)));
const pageRows = computed(() =>
  cases.value.slice((page.value - 1) * pageSize, page.value * pageSize));

// ---- bank meta editing (admin/owner) ----
const editing = ref(false);
const form = ref<any>({});
function startEdit() {
  const req = bank.value.requirements || {};
  form.value = {
    display_name: bank.value.display_name, category: bank.value.category,
    description_zh: bank.value.description_zh, description_en: bank.value.description_en,
    source_note: bank.value.source_note,
    default_level: bank.value.default_level || "L2",
    req_profile: req.profile || "", req_materials: req.materials || "",
    req_tool_strict: req.tool_strict === false ? "no" : "yes",
  };
  editing.value = true;
}
async function saveMeta() {
  err.value = ""; msg.value = "";
  try {
    bank.value = await api("PATCH", "/benchmarks/" + props.name, {
      ...form.value,
      requirements: {
        profile: form.value.req_profile.trim(),
        tool_strict: form.value.req_tool_strict !== "no",
        materials: form.value.req_materials.trim(),
      },
    });
    editing.value = false;
  } catch (e: any) { err.value = e.message; }
}
async function toggleOnline() {
  err.value = "";
  try {
    bank.value = await api("PATCH", "/benchmarks/" + props.name,
      { status: bank.value.status === "online" ? "offline" : "online" });
  } catch (e: any) { err.value = e.message; }
}
async function deleteBank() {
  if (!confirm(t("common.confirmDelete"))) return;
  try {
    await api("DELETE", "/benchmarks/" + props.name);
    router.push("/banks");
  } catch (e: any) { err.value = e.message; }
}

// ---- member overrides (public banks) ----
async function putOverride(cid: string, level: string | null, enabled: boolean | null) {
  err.value = "";
  try {
    await api("PUT", `/benchmarks/${props.name}/my-overrides`,
      { overrides: [{ case_id: cid, level, enabled }] });
    await load();
  } catch (e: any) { err.value = e.message; }
}
async function restoreDefaults() {
  err.value = ""; msg.value = "";
  try {
    const r = await api("DELETE", `/benchmarks/${props.name}/my-overrides`);
    msg.value = "cleared: " + r.cleared;
    await load();
  } catch (e: any) { err.value = e.message; }
}

async function load() {
  err.value = "";
  try {
    bank.value = await api("GET", "/benchmarks/" + props.name);
    const c = await api("GET", `/benchmarks/${props.name}/cases`);
    cases.value = c.cases;
  } catch (e: any) { err.value = e.message; }
}
onMounted(load);
</script>

<template>
  <div class="inline" style="margin-bottom: 8px">
    <router-link class="btn small" to="/banks">{{ $t("overview.back") }}</router-link>
    <h2 class="page-title" style="margin: 0">{{ bank?.display_name || name }}</h2>
    <span v-if="bank" class="chip" :class="bank.visibility">{{ $t("banks." + bank.visibility) }}</span>
    <span v-if="bank" class="chip" :class="bank.status">{{ $t("status." + bank.status) }}</span>
    <span class="muted">{{ bank?.category || $t("banks.uncategorized") }}</span>
    <span v-if="bank?.requirements?.profile" class="chip private">
      {{ $t("detail.reqProfile") }}: {{ bank.requirements.profile }}</span>
    <router-link v-if="canManage" class="btn small primary" :to="'/banks/' + name + '/edit'">
      {{ $t("edit.title") }}</router-link>
    <router-link class="btn small" :to="`/banks/${name}/overview`">{{ $t("banks.overview") }}</router-link>
  </div>
  <div v-if="err" class="error-box">{{ err }}</div>
  <div v-if="msg" class="ok-box">{{ msg }}</div>

  <div class="card" v-if="bank">
    <div class="inline">
      <span class="muted">{{ $t("banks.cases") }}: <b>{{ bank.case_count }}</b></span>
      <span class="muted">{{ $t("banks.levelDist") }}: {{ bank.level_dist?.L0 || 0 }}/{{ bank.level_dist?.L1 || 0 }}/{{ bank.level_dist?.L2 || 0 }}</span>
      <span class="muted">{{ $t("banks.source") }}: {{ bank.source_note || "-" }}</span>
      <span class="spacer" style="flex:1"></span>
      <button v-if="canManage" class="btn small" @click="startEdit">{{ $t("detail.editBank") }}</button>
      <button v-if="canManage && isPublic" class="btn small" @click="toggleOnline">
        {{ $t("detail.onlineToggle." + (bank.status === "online" ? "online" : "offline")) }}</button>
      <button v-if="canManage" class="btn small danger" @click="deleteBank">{{ $t("detail.deleteBank") }}</button>
    </div>
    <div class="muted" style="margin-top: 6px">{{ $i18n.locale === 'en' ? bank.description_en : bank.description_zh }}</div>
    <div v-if="editing" style="margin-top: 10px">
      <div class="form-grid">
        <div class="form-row"><label>{{ $t("create.display") }}</label><input v-model="form.display_name" /></div>
        <div class="form-row"><label>{{ $t("create.category") }}</label><input v-model="form.category" /></div>
        <div class="form-row"><label>{{ $t("create.defaultLevel") }}</label>
          <Sel v-model="form.default_level" :options="LEVELS" /></div>
        <div class="form-row"><label>{{ $t("create.sourceNote") }}</label><input v-model="form.source_note" /></div>
      </div>
      <h4 class="section-title">{{ $t("detail.reqProfile") }} / {{ $t("detail.reqMaterials") }} / {{ $t("detail.reqToolStrict") }}</h4>
      <div class="form-grid">
        <div class="form-row"><label>{{ $t("detail.reqProfile") }}</label>
          <input v-model="form.req_profile" class="mono" placeholder="fb / bank / locomo / tau-airline" /></div>
        <div class="form-row"><label>{{ $t("detail.reqToolStrict") }}</label>
          <Sel v-model="form.req_tool_strict" :options="STRICT" /></div>
      </div>
      <div class="form-row"><label>{{ $t("detail.reqMaterials") }}</label>
        <textarea v-model="form.req_materials" rows="2" /></div>
      <div class="form-row"><label>{{ $t("create.descZh") }}</label><textarea v-model="form.description_zh" /></div>
      <div class="form-row"><label>{{ $t("create.descEn") }}</label><textarea v-model="form.description_en" /></div>
      <div class="inline">
        <button class="btn primary" @click="saveMeta">{{ $t("common.save") }}</button>
        <button class="btn" @click="editing = false">{{ $t("common.cancel") }}</button>
      </div>
    </div>
    <div class="muted" style="margin-top: 8px" v-if="isPublic">
      {{ $t("detail.conceptHint") }}
    </div>
    <div class="muted" style="margin-top: 4px" v-if="bank?.requirements?.materials">
      {{ $t("detail.reqMaterials") }}: {{ bank.requirements.materials }}
    </div>
  </div>

  <div class="card" v-if="isPublic && hasMin(store.user?.role, 'member')">
    <div class="inline">
      <h4 style="margin: 0">{{ $t("detail.myConfig") }}</h4>
      <span class="spacer" style="flex:1"></span>
      <button class="btn small" @click="restoreDefaults">{{ $t("detail.restoreDefaults") }}</button>
    </div>
  </div>

  <div class="tbl-wrap">
  <table class="tbl">
    <thead><tr>
      <th>{{ $t("detail.colCase") }}</th>
      <th>{{ $t("detail.colQuery") }}</th>
      <th :title="$t('detail.levelHint')">{{ $t("detail.colLevel") }}</th>
      <th v-if="canManage" :title="$t('detail.statusHint')">{{ $t("detail.colStatus") }}</th>
      <th v-if="isPublic" :title="$t('detail.myLevelHint')">{{ $t("detail.colMyLevel") }}</th>
      <th v-if="isPublic" :title="$t('detail.myRunHint')">{{ $t("detail.colRun") }}</th>
      <th>{{ $t("detail.colLast") }}</th>
    </tr></thead>
    <tbody>
      <tr v-if="!pageRows.length"><td colspan="7" class="muted">{{ $t("detail.noCases") }}</td></tr>
      <tr v-for="c in pageRows" :key="c.case_id">
        <td class="mono">{{ c.case_id }}<div v-if="c.status === 'retired'" class="muted">{{ $t("detail.retired") }}</div></td>
        <td>{{ c.query_preview }}</td>
        <td class="mono">{{ c.level }}</td>
        <td v-if="canManage" class="mono">{{ c.status }}</td>
        <td v-if="isPublic">
          <Sel v-if="hasMin(store.user?.role, 'member')" small
               :model-value="c.my_level || ''" :options="MY_LEVELS"
               @update:model-value="(v: string) => putOverride(c.case_id, v || null, null)" />
          <span v-else class="mono">{{ c.my_level || "-" }}</span>
        </td>
        <td v-if="isPublic">
          <input v-if="hasMin(store.user?.role, 'member')" type="checkbox" :checked="!!c.my_enabled"
                 @change="putOverride(c.case_id, null, ($event.target as HTMLInputElement).checked)" />
          <span v-else>{{ c.my_enabled ? $t("common.yes") : $t("common.no") }}</span>
        </td>
        <td><span v-if="c.last_verdict" class="chip" :class="c.last_verdict">{{ $t("verdict." + c.last_verdict) }}<span v-if="c.last_score" class="mono"> · {{ c.last_score }}</span></span><span v-else class="muted">-</span></td>
      </tr>
    </tbody>
  </table>
  </div>
  <div class="pager" v-if="pages > 1">
    <button class="btn small" :disabled="page <= 1" @click="page--">{{ $t("common.prev") }}</button>
    <span class="muted">{{ page }} / {{ pages }}</span>
    <button class="btn small" :disabled="page >= pages" @click="page++">{{ $t("common.next") }}</button>
  </div>

</template>
