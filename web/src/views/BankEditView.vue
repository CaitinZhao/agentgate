<script setup lang="ts">
// Bank edit page (doc 33 W6): online add/edit/delete of cases with a staged-changes draft
// (one draft per user per bank; changes take effect only after the summary confirm), plus
// the Excel / external-dataset batch intake. Manage rights only (public: admin+, private:
// owner+).
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import CaseForm from "../components/CaseForm.vue";
import Sel from "../components/Sel.vue";
import { api, download, guideUrl } from "../store";

const { t } = useI18n();
const router = useRouter();
const props = defineProps<{ name: string }>();

const bank = ref<any>(null);
const cases = ref<any[]>([]);
const err = ref("");
const msg = ref("");


// ---- staged changes (the draft) ----
const newCases = ref<any[]>([]);           // [{payload, label}]
const edits = ref<Record<string, any>>({}); // case_id -> {payload, label}
const deletes = ref<string[]>([]);         // case_ids
const dirty = computed(() =>
  newCases.value.length + Object.keys(edits.value).length + deletes.value.length);

const LEVELS = [
  { value: "L0", label: "L0" }, { value: "L1", label: "L1" }, { value: "L2", label: "L2" }];
const STATUS = [
  { value: "active", label: t("edit.statusActive") },
  { value: "retired", label: t("edit.statusRetired") }];

// ---- add new ----
const showAdd = ref(false);
function addNew(payload: any) {
  newCases.value.unshift(payload);          // newest at the top of the list
  showAdd.value = false;
}

// ---- edit existing ----
const editingId = ref("");
function stageEdit(caseId: string, payload: any) {
  edits.value = { ...edits.value, [caseId]: { payload, label: payload.query } };
  editingId.value = "";
}
function isEdited(caseId: string) { return !!edits.value[caseId]; }
function unEdit(caseId: string) {
  const e = { ...edits.value }; delete e[caseId]; edits.value = e;
}
function isDeleted(caseId: string) { return deletes.value.includes(caseId); }
function toggleDelete(caseId: string) {
  deletes.value = isDeleted(caseId)
    ? deletes.value.filter((c) => c !== caseId)
    : [...deletes.value, caseId];
}

// ---- draft ----
const showDraftPrompt = ref(false);
const draftLoaded = ref<any>(null);
async function loadDraftState() {
  try {
    const r = await api("GET", `/benchmarks/${props.name}/edit-draft`);
    if (r.draft && (r.draft.new_cases?.length || Object.keys(r.draft.edits || {}).length
        || r.draft.deletes?.length)) {
      draftLoaded.value = r.draft;
      showDraftPrompt.value = true;
    }
  } catch { /* no draft / no rights — fine */ }
}
function applyDraft() {
  const d = draftLoaded.value || { new_cases: [], edits: {}, deletes: [] };
  newCases.value = d.new_cases || [];
  edits.value = d.edits || {};
  deletes.value = d.deletes || [];
  showDraftPrompt.value = false;
  msg.value = t("edit.draftLoaded");
}
async function discardDraft() {
  showDraftPrompt.value = false;
  try { await api("DELETE", `/benchmarks/${props.name}/edit-draft`); } catch { /* ignore */ }
  draftLoaded.value = null;
}
async function saveDraft() {
  try {
    await api("PUT", `/benchmarks/${props.name}/edit-draft`, { draft: {
      new_cases: newCases.value, edits: edits.value, deletes: deletes.value } });
    msg.value = t("edit.draftSaved");
    confirmOpen.value = false;
  } catch (e: any) { err.value = e.message; }
}

// ---- commit flow: summary dialog -> apply ----
const confirmOpen = ref(false);
function openConfirm() {
  err.value = "";
  if (!dirty.value) { err.value = t("edit.nothing"); return; }
  confirmOpen.value = true;
}
function applyAll(): Promise<void> {
  return (async () => {
    for (const nc of newCases.value) {
      await api("POST", `/benchmarks/${props.name}/cases`, nc);
    }
    for (const [cid, e] of Object.entries(edits.value)) {
      await api("PATCH", `/benchmarks/${props.name}/cases/${cid}`, e.payload);
    }
    for (const cid of deletes.value) {
      await api("DELETE", `/benchmarks/${props.name}/cases/${cid}`);
    }
  })();
}
async function confirmApply() {
  err.value = "";
  try {
    await applyAll();
    await api("DELETE", `/benchmarks/${props.name}/edit-draft`);
    confirmOpen.value = false;
    msg.value = t("edit.applied", { n: dirty.value });
    newCases.value = []; edits.value = {}; deletes.value = [];
    await load();
  } catch (e: any) { err.value = e.message; }
}

// ---- excel / external dataset (batch) ----
const excelFile = ref<File | null>(null);
const importResult = ref<any>(null);
async function importExcel() {
  if (!excelFile.value) return;
  err.value = ""; importResult.value = null;
  const fd = new FormData(); fd.append("file", excelFile.value);
  try {
    const r = await api("POST", `/benchmarks/${props.name}/cases/bulk`, fd);
    importResult.value = r;
    if (r.status === "ok") await load();
  } catch (e: any) { err.value = e.message; }
}
function dlTemplate(blank: boolean) {
  download(`/benchmarks/${props.name}/cases/template?blank=${blank}`,
    blank ? "case-template.xlsx" : props.name + "-cases.xlsx").catch((e) => (err.value = e.message));
}
const adapt = ref<any>({ file: null, url: "", id_field: "id", question_field: "question",
  answer_field: "answer", doc_field: "", profile: "", level: "L1", llm_hint: false });
const showAdapt = ref(false);
const adaptResult = ref<any>(null);
async function runAdapt() {
  if (!adapt.value.file && !adapt.value.url.trim()) return;
  err.value = ""; adaptResult.value = null;
  const q = new URLSearchParams({
    id_field: adapt.value.id_field, question_field: adapt.value.question_field,
    answer_field: adapt.value.answer_field, doc_field: adapt.value.doc_field,
    profile: adapt.value.profile, level: adapt.value.level,
    llm_hint: String(adapt.value.llm_hint),
  });
  if (adapt.value.url.trim()) q.set("source_url", adapt.value.url.trim());
  const fd = new FormData();
  if (adapt.value.file) fd.append("file", adapt.value.file);
  try {
    adaptResult.value = await api("POST", `/benchmarks/${props.name}/autoadapt?` + q.toString(), fd);
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

// ---- domain pack (doc 35 §4.2) ----
const pack = ref<any>(null);
const showPackEdit = ref(false);
const packText = ref("");
const aiPackOpen = ref(false);
const aiPack = ref({ description: "", samples: "" });
const aiPackBusy = ref(false);
const aiPackErr = ref("");
const aiPackDraft = ref<any>(null);
const aiPackDraftText = ref("");
async function loadPack() {
  try {
    const r = await api("GET", `/benchmarks/${props.name}/pack`);
    pack.value = r.pack;
    packText.value = JSON.stringify(r.pack, null, 2);
  } catch { /* viewer role etc. */ }
}
async function savePack() {
  err.value = ""; msg.value = "";
  try {
    const body = JSON.parse(packText.value);
    const r = await api("PUT", `/benchmarks/${props.name}/pack`, { pack: body });
    msg.value = t("edit.packSaved", { id: r.pack_id });
    showPackEdit.value = false;
    await loadPack();
  } catch (e: any) { err.value = t("edit.packSaveError") + ": " + e.message; }
}
async function runPackDraft() {
  aiPackErr.value = ""; aiPackBusy.value = true; aiPackDraft.value = null;
  try {
    const r = await api("POST", `/benchmarks/${props.name}/ai-draft-pack`, aiPack.value);
    aiPackDraft.value = r.draft;
    aiPackDraftText.value = JSON.stringify(r.draft, null, 2);
  } catch (e: any) { aiPackErr.value = e.message; }
  aiPackBusy.value = false;
}
async function savePackDraft() {
  err.value = ""; msg.value = "";
  try {
    const body = JSON.parse(aiPackDraftText.value);
    body.authored_by = "llm-draft+human-reviewed";     // saving = the human reviewed it
    const r = await api("PUT", `/benchmarks/${props.name}/pack`, { pack: body });
    msg.value = t("edit.packSaved", { id: r.pack_id });
    aiPackOpen.value = false; aiPackDraft.value = null;
    await loadPack();
  } catch (e: any) { err.value = t("edit.packSaveError") + ": " + e.message; }
}
onMounted(async () => { await load(); await loadDraftState(); await loadPack(); });
</script>

<template>
  <div class="inline" style="margin-bottom: 8px">
    <router-link class="btn small" :to="'/banks/' + name">{{ name }} · {{ $t("detail.detail") }}</router-link>
    <h2 class="page-title" style="margin: 0">{{ $t("edit.title") }}: {{ bank?.display_name || name }}</h2>
    <span class="chip" v-if="dirty" style="background:#fef9c3;color:#854d0e">
      {{ $t("edit.uncommitted") }}: {{ dirty }}</span>
  </div>
  <div v-if="err" class="error-box">{{ err }}</div>
  <div v-if="msg" class="ok-box">{{ msg }}</div>

  <dialog :open="showDraftPrompt">
    <h4>{{ $t("edit.draftFound") }}</h4>
    <p class="muted">{{ $t("edit.draftFoundHint") }}</p>
    <div class="inline">
      <button class="btn primary" @click="applyDraft">{{ $t("edit.draftLoad") }}</button>
      <button class="btn danger" @click="discardDraft">{{ $t("edit.draftDiscard") }}</button>
    </div>
  </dialog>

  <div class="card">
    <h4>{{ $t("edit.onlineSection") }}</h4>
    <div class="inline" style="margin-bottom: 10px">
      <button class="btn primary" @click="showAdd = !showAdd">{{ $t("edit.addCase") }}</button>
      <button class="btn primary" :disabled="!dirty" @click="openConfirm">{{ $t("edit.commit") }}</button>
      <span class="muted">
        {{ $t("edit.summaryCounts", { a: newCases.length, m: Object.keys(edits).length, d: deletes.length }) }}
      </span>
      <span class="spacer" style="flex:1"></span>
      <a :href="guideUrl('bank-edit')" target="_blank" class="muted">{{ $t("detail.caseGuide") }}</a>
    </div>

    <div class="card" v-if="showAdd" style="background:#f8fafc">
      <CaseForm :bank-name="name" @submit="addNew" @cancel="showAdd = false" />
    </div>

    <div class="card" v-if="editingId" style="background:#f8fafc">
      <h4>{{ $t("edit.editing") }}: {{ editingId }}</h4>
      <CaseForm :bank-name="name" :initial="cases.find(c => c.case_id === editingId)" :case-id-locked="true"
                @submit="(p: any) => stageEdit(editingId, p)"
                @cancel="editingId = ''" />
    </div>

    <div class="tbl-wrap">
    <table class="tbl">
      <thead><tr>
        <th>{{ $t("edit.colState") }}</th><th>{{ $t("detail.colCase") }}</th>
        <th>{{ $t("detail.colQuery") }}</th><th>{{ $t("detail.colLevel") }}</th>
        <th>{{ $t("detail.colStatus") }}</th><th>{{ $t("common.actions") }}</th>
      </tr></thead>
      <tbody>
        <tr v-if="!cases.length && !newCases.length"><td colspan="6" class="muted">{{ $t("detail.noCases") }}</td></tr>
        <tr v-for="(nc, i) in newCases" :key="'new' + i" style="background:#f0fdf4">
          <td><span class="chip PASS">{{ $t("edit.tagNew") }}</span></td>
          <td class="mono">{{ nc.case_id || "-" }}</td>
          <td>{{ nc.query }}</td><td class="mono">{{ nc.level }}</td><td class="mono">{{ nc.status }}</td>
          <td><button class="btn small" @click="newCases.splice(i, 1)">{{ $t("common.cancel") }}</button></td>
        </tr>
        <tr v-for="c in cases" :key="c.case_id"
            :style="isDeleted(c.case_id) ? 'text-decoration:line-through;opacity:.5' : ''">
          <td>
            <span v-if="isEdited(c.case_id)" class="chip PENDING">{{ $t("edit.tagEdited") }}</span>
            <span v-if="isDeleted(c.case_id)" class="chip FAIL">{{ $t("edit.tagDeleted") }}</span>
          </td>
          <td class="mono">{{ c.case_id }}</td>
          <td>{{ isEdited(c.case_id) ? edits[c.case_id].payload.query : c.query_preview }}</td>
          <td>
            <Sel small :model-value="isEdited(c.case_id) ? edits[c.case_id].payload.level : c.level"
                 :options="LEVELS"
                 @update:model-value="(v: string) => stageEdit(c.case_id, {
                   ...(edits[c.case_id] ? edits[c.case_id].payload : {}),
                   case_id: c.case_id, query: (edits[c.case_id] ? edits[c.case_id].payload.query : c.query_preview),
                   level: v, status: (edits[c.case_id] ? edits[c.case_id].payload.status : c.status) })" />
          </td>
          <td>
            <Sel small :model-value="isEdited(c.case_id) ? edits[c.case_id].payload.status : c.status"
                 :options="STATUS"
                 @update:model-value="(v: string) => stageEdit(c.case_id, {
                   ...(edits[c.case_id] ? edits[c.case_id].payload : {}),
                   case_id: c.case_id, query: (edits[c.case_id] ? edits[c.case_id].payload.query : c.query_preview),
                   level: (edits[c.case_id] ? edits[c.case_id].payload.level : c.level), status: v })" />
          </td>
          <td style="white-space: nowrap">
            <button class="btn small" @click="editingId = c.case_id">{{ $t("common.edit") }}</button>
            <button v-if="isEdited(c.case_id)" class="btn small" @click="unEdit(c.case_id)">{{ $t("edit.undo") }}</button>
            <button class="btn small danger" @click="toggleDelete(c.case_id)">
              {{ isDeleted(c.case_id) ? $t("edit.undo") : $t("common.delete") }}</button>
          </td>
        </tr>
      </tbody>
    </table>
    </div>
  </div>

  <div class="card">
    <h4>{{ $t("edit.batchSection") }}</h4>
    <div class="inline" style="margin-bottom: 8px">
      <button class="btn small" @click="dlTemplate(true)">{{ $t("detail.downloadBlank") }}</button>
      <button class="btn small" @click="dlTemplate(false)">{{ $t("detail.downloadBank") }}</button>
      <input type="file" accept=".xlsx" @change="(e: any) => excelFile = e.target.files[0]" />
      <button class="btn small primary" :disabled="!excelFile" @click="importExcel">{{ $t("detail.excel") }}</button>
    </div>
    <div v-if="importResult && importResult.status === 'ok'" class="ok-box">
      {{ $t("detail.importOk", { n: importResult.imported }) }}
    </div>
    <div v-if="importResult && importResult.status === 'problems'" class="error-box">
      {{ $t("detail.importProblems", { n: importResult.problems.length }) }}
      <button class="btn small" style="margin-left: 8px"
              @click="download(importResult.marked_download, 'marked.xlsx')">{{ $t("detail.markedFile") }}</button>
      <table class="tbl" style="margin-top: 8px">
        <thead><tr><th>{{ $t("detail.problemRow") }}</th><th>{{ $t("detail.problemMsg") }}</th></tr></thead>
        <tbody><tr v-for="p in importResult.problems" :key="p.row + p.msg">
          <td>{{ p.row }}</td><td>{{ p.msg }}</td></tr></tbody>
      </table>
    </div>

    <div class="inline" style="margin-top: 10px">
      <button class="btn small" @click="showAdapt = !showAdapt">{{ $t("detail.adaptTitle") }}</button>
      <a :href="guideUrl('bank-create')" target="_blank" class="muted">{{ $t("detail.bankGuide") }}</a>
    </div>
    <div v-if="showAdapt">
      <div class="form-row"><label>{{ $t("edit.adaptUrl") }}</label>
        <input v-model="adapt.url" class="mono" placeholder="https://.../dataset.jsonl" /></div>
      <div class="inline">
        <input type="file" accept=".jsonl,.csv" @change="(e: any) => adapt.file = e.target.files[0]" />
        <span class="muted">{{ $t("edit.adaptOr") }}</span>
      </div>
      <div class="form-grid" style="margin-top: 8px">
        <div class="form-row"><label>{{ $t("detail.idField") }}</label><input v-model="adapt.id_field" /></div>
        <div class="form-row"><label>{{ $t("detail.qField") }}</label><input v-model="adapt.question_field" /></div>
        <div class="form-row"><label>{{ $t("detail.aField") }}</label><input v-model="adapt.answer_field" /></div>
        <div class="form-row"><label>{{ $t("detail.dField") }}</label><input v-model="adapt.doc_field" /></div>
        <div class="form-row"><label>{{ $t("detail.profile") }}</label><input v-model="adapt.profile" /></div>
        <div class="form-row"><label>{{ $t("detail.level") }}</label>
          <Sel v-model="adapt.level" :options="LEVELS" /></div>
      </div>
      <label class="checkbar"><input type="checkbox" v-model="adapt.llm_hint" /> {{ $t("detail.llmHint") }}</label>
      <div style="margin-top: 6px">
        <button class="btn primary" :disabled="!adapt.file && !adapt.url.trim()" @click="runAdapt">
          {{ $t("detail.autoadapt") }}</button>
      </div>
    </div>
    <div v-if="adaptResult" class="ok-box">
      {{ $t("detail.adaptOk", { n: adaptResult.cases, b: JSON.stringify(adaptResult.buckets) }) }}
    </div>
  </div>

  <!-- domain pack (doc 35 §4.2): bank-level judging config inherited by all cases -->
  <div class="card">
    <div class="inline" style="margin-bottom: 6px">
      <h4 style="margin: 0">{{ $t("edit.packSection") }}</h4>
      <span class="chip" v-if="pack">{{ pack.pack_id }} · v{{ pack.version || "?" }}</span>
      <span class="chip muted" v-if="pack?.authored_by"
            :title="$t('edit.packAuthoredHint')" style="text-transform:none">
        {{ $t("edit.packAuthored") }}: {{ pack.authored_by }}</span>
      <span class="spacer" style="flex:1"></span>
      <button class="btn small" @click="showPackEdit = !showPackEdit">{{ $t("edit.packEdit") }}</button>
      <button class="btn small" @click="aiPackOpen = true">✨ {{ $t("edit.packAiDraft") }}</button>
    </div>
    <div v-if="pack" class="muted">
      {{ $t("edit.packProfile") }}: <span class="mono">{{ pack.profile || "-" }}</span>
      · {{ $t("edit.packEvidence") }}: <b>{{ pack.evidence_required ? $t("common.yes") : $t("common.no") }}</b>
      · {{ $t("edit.packCaliber") }}: <b>{{ pack.caliber_required ? $t("common.yes") : $t("common.no") }}</b>
      · {{ $t("edit.packRedlines") }}:
      <span class="mono">{{ (pack.red_lines?.forbidden_tools || []).concat(pack.red_lines?.forbidden_answer_regex || []).join("；") || "-" }}</span>
    </div>
    <div v-if="showPackEdit" style="margin-top: 8px">
      <div class="form-row"><label>pack JSON</label>
        <textarea v-model="packText" rows="12" class="mono" style="font-size: 12px" /></div>
      <button class="btn primary small" @click="savePack">{{ $t("edit.packSave") }}</button>
      <span class="muted" style="margin-left: 8px">{{ $t("edit.packSaveHint") }}</span>
    </div>
  </div>

  <dialog :open="aiPackOpen" style="min-width: 520px">
    <h4>{{ $t("edit.packAiTitle") }}</h4>
    <p class="muted">{{ $t("edit.packAiHint") }}</p>
    <div class="form-row"><label>{{ $t("edit.packAiDesc") }}</label>
      <textarea v-model="aiPack.description" rows="4"
                placeholder="例：车险/寿险条款问答，材料=条款 PDF 库，由检索工具提供" /></div>
    <div class="form-row"><label>{{ $t("edit.packAiSamples") }}</label>
      <textarea v-model="aiPack.samples" rows="5"
                placeholder="3~5 道样例题（含一条材料原文示例）" /></div>
    <div v-if="aiPackErr" class="error-box">{{ aiPackErr }}</div>
    <div v-if="aiPackDraft" class="form-row"><label>{{ $t("edit.packAiResult") }}</label>
      <textarea v-model="aiPackDraftText" rows="10" class="mono" style="font-size: 12px" /></div>
    <div class="inline" style="margin-top: 8px">
      <button v-if="!aiPackDraft" class="btn primary" :disabled="aiPackBusy || !aiPack.description.trim()"
              @click="runPackDraft">{{ aiPackBusy ? $t("common.loading") : $t("edit.packAiGo") }}</button>
      <template v-else>
        <button class="btn primary" @click="savePackDraft">{{ $t("edit.packAiReviewedSave") }}</button>
        <span class="muted">{{ $t("edit.packAiReviewHint") }}</span>
      </template>
      <button class="btn" @click="aiPackOpen = false; aiPackDraft = null">{{ $t("common.close") }}</button>
    </div>
  </dialog>

  <dialog :open="confirmOpen" style="min-width: 460px">
    <h4>{{ $t("edit.confirmTitle") }}</h4>
    <table class="tbl">
      <tbody>
        <tr><td>{{ $t("edit.tagNew") }}</td><td>{{ newCases.length }}</td></tr>
        <tr><td>{{ $t("edit.tagEdited") }}</td><td>{{ Object.keys(edits).length }}</td></tr>
        <tr><td>{{ $t("edit.tagDeleted") }}</td><td>{{ deletes.length }}</td></tr>
      </tbody>
    </table>
    <p class="muted">{{ $t("edit.confirmHint") }}</p>
    <div v-if="err" class="error-box">{{ err }}</div>
    <div class="inline" style="margin-top: 8px">
      <button class="btn primary" @click="confirmApply">{{ $t("edit.confirmApply") }}</button>
      <button class="btn" @click="confirmOpen = false">{{ $t("edit.backToEdit") }}</button>
      <button class="btn" @click="saveDraft">{{ $t("edit.saveDraft") }}</button>
    </div>
  </dialog>

</template>
