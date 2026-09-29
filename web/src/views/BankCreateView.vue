<script setup lang="ts">
import { ref } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import Sel from "../components/Sel.vue";
import { api, download, guideUrl } from "../store";

const { t } = useI18n();
const router = useRouter();
const LEVELS = [
  { value: "L0", label: "L0" }, { value: "L1", label: "L1" }, { value: "L2", label: "L2" }];
const VISIBILITY = [
  { value: "private", label: t("create.private") },
  { value: "public", label: t("create.public") }];
const step = ref(1);
const name = ref("");
const form = ref<any>({
  display_name: "", visibility: "private", category: "", default_level: "L2",
  description_zh: "", description_en: "", source_note: "",
});
const err = ref("");
const msg = ref("");
const method = ref<"empty" | "excel" | "adapt">("empty");
const excelFile = ref<File | null>(null);
const adaptFile = ref<File | null>(null);
const adapt = ref<any>({ id_field: "id", question_field: "question", answer_field: "answer",
  doc_field: "", profile: "", level: "L1", llm_hint: false });
const importResult = ref<any>(null);
const adaptResult = ref<any>(null);
const confirmEmpty = ref(false);

function nextStep() {
  if (step.value === 2 && method.value === "empty" && !importResult.value && !adaptResult.value) {
    confirmEmpty.value = true;           // W6: confirm before skipping case creation
    return;
  }
  step.value = 3;
}
function skipAdd() {
  confirmEmpty.value = false;
  step.value = 3;
}

async function createBank() {
  err.value = ""; msg.value = "";
  try {
    await api("POST", "/benchmarks", { name: name.value, ...form.value });
    msg.value = t("create.created");
    step.value = 2;
  } catch (e: any) { err.value = e.message; }
}

async function importExcel() {
  if (!excelFile.value) return;
  err.value = ""; importResult.value = null;
  const fd = new FormData();
  fd.append("file", excelFile.value);
  try {
    const r = await api("POST", `/benchmarks/${name.value}/cases/bulk`, fd);
    importResult.value = r;
  } catch (e: any) { err.value = e.message; }
}

async function runAdapt() {
  if (!adaptFile.value) return;
  err.value = ""; adaptResult.value = null;
  const fd = new FormData();
  fd.append("file", adaptFile.value);
  try {
    const q = new URLSearchParams({
      id_field: adapt.value.id_field, question_field: adapt.value.question_field,
      answer_field: adapt.value.answer_field, doc_field: adapt.value.doc_field,
      profile: adapt.value.profile, level: adapt.value.level,
      llm_hint: String(adapt.value.llm_hint),
    }).toString();
    adaptResult.value = await api("POST", `/benchmarks/${name.value}/autoadapt?` + q, fd);
  } catch (e: any) { err.value = e.message; }
}

function dlTemplate(blank: boolean) {
  download(`/benchmarks/${name.value}/cases/template?blank=${blank}`, "template.xlsx")
    .catch((e) => (err.value = e.message));
}

function dlMarked() {
  if (importResult.value?.marked_download) {
    download(importResult.value.marked_download, "marked.xlsx")
      .catch((e) => (err.value = e.message));
  }
}
</script>

<template>
  <h2 class="page-title">{{ $t("create.title") }}</h2>
  <div class="muted" style="margin-bottom: 10px">{{ ["", $t("create.step1"), $t("create.step2"), $t("create.step3")][step] }}</div>
  <div v-if="err" class="error-box">{{ err }}</div>
  <div v-if="msg" class="ok-box">{{ msg }}</div>

  <div class="card" v-if="step === 1">
    <div class="form-grid">
      <div class="form-row"><label>{{ $t("create.name") }}</label><input v-model="name" class="mono" placeholder="my-suite" /></div>
      <div class="form-row"><label>{{ $t("create.display") }}</label><input v-model="form.display_name" /></div>
      <div class="form-row"><label>{{ $t("create.visibility") }}</label>
        <Sel v-model="form.visibility" :options="VISIBILITY" /></div>
      <div class="form-row"><label>{{ $t("create.category") }}</label><input v-model="form.category" /></div>
      <div class="form-row"><label>{{ $t("create.defaultLevel") }}</label>
        <Sel v-model="form.default_level" :options="LEVELS" /></div>
      <div class="form-row"><label>{{ $t("create.sourceNote") }}</label><input v-model="form.source_note" /></div>
    </div>
    <div class="form-row"><label>{{ $t("create.descZh") }}</label><textarea v-model="form.description_zh" /></div>
    <div class="form-row"><label>{{ $t("create.descEn") }}</label><textarea v-model="form.description_en" /></div>
    <button class="btn primary" @click="createBank">{{ $t("create.next") }}</button>
  </div>

  <div class="card" v-if="step === 2">
    <div class="checkbar" style="margin-bottom: 10px">
      <button class="btn small" :class="{ primary: method === 'empty' }" @click="method = 'empty'">{{ $t("create.buildEmpty") }}</button>
      <button class="btn small" :class="{ primary: method === 'excel' }" @click="method = 'excel'">{{ $t("create.buildExcel") }}</button>
      <button class="btn small" :class="{ primary: method === 'adapt' }" @click="method = 'adapt'">{{ $t("create.buildAdapt") }}</button>
    </div>

    <div v-if="method === 'excel'">
      <div class="inline" style="margin-bottom: 8px">
        <button class="btn small" @click="dlTemplate(true)">{{ $t("detail.downloadBlank") }}</button>
      </div>
      <div class="inline">
        <input type="file" accept=".xlsx" @change="(e: any) => excelFile = e.target.files[0]" />
        <button class="btn primary" :disabled="!excelFile" @click="importExcel">{{ $t("detail.excel") }}</button>
      </div>
      <div v-if="importResult && importResult.status === 'ok'" class="ok-box">{{ $t("detail.importOk", { n: importResult.imported }) }}</div>
      <div v-if="importResult && importResult.status === 'problems'" class="error-box">
        {{ $t("detail.importProblems", { n: importResult.problems.length }) }}
        <button class="btn small" style="margin-left: 8px" @click="dlMarked()">{{ $t("detail.markedFile") }}</button>
      </div>
    </div>

    <div v-if="method === 'adapt'">
      <div class="form-row"><label>{{ $t("edit.adaptUrl") }}</label>
        <input v-model="adapt.url" class="mono" placeholder="https://.../dataset.jsonl" /></div>
      <div class="inline">
        <input type="file" accept=".jsonl,.csv" @change="(e: any) => adaptFile = e.target.files[0]" />
        <span class="muted">{{ $t("edit.adaptOr") }}</span>
        <a :href="guideUrl('bank-create')" target="_blank" class="muted">{{ $t("create.adaptGuide") }}</a>
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
      <div class="inline" style="margin-top: 8px">
        <button class="btn primary" :disabled="!adaptFile && !adapt.url.trim()" @click="runAdapt">{{ $t("detail.autoadapt") }}</button>
      </div>
      <div v-if="adaptResult" class="ok-box">
        {{ $t("detail.adaptOk", { n: adaptResult.cases, b: JSON.stringify(adaptResult.buckets) }) }}
      </div>
    </div>

    <div style="margin-top: 14px">
      <button class="btn primary" @click="nextStep">{{ $t("create.next") }}</button>
      <a :href="guideUrl('bank-create')" target="_blank" class="muted" style="margin-left: 10px">{{ $t("detail.bankGuide") }}</a>
    </div>
  </div>

  <dialog :open="confirmEmpty" style="min-width: 420px">
    <h4>{{ $t("create.emptyConfirm") }}</h4>
    <p class="muted">{{ $t("create.emptyConfirmHint") }}</p>
    <div class="inline">
      <button class="btn primary" @click="skipAdd">{{ $t("common.ok") }}</button>
      <button class="btn" @click="confirmEmpty = false">{{ $t("edit.backToEdit") }}</button>
    </div>
  </dialog>
</template>
