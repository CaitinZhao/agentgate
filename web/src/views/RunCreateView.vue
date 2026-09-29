<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { api, guideUrl, store } from "../store";

const { t } = useI18n();
const banks = ref<any[]>([]);
const err = ref("");
const taskName = ref("");
const targetUrl = ref("");
const proxy = ref(true);          // message-level recording default ON (doc 34/35)
const scheduled = ref("");
const stability = ref(false);
const stabilityK = ref(2);
const aiAssist = ref(true);   // default on; effective only when the user has AI configured
const levels = ref<string[]>([]);
const caseIds = ref("");
const selected = ref<Record<string, boolean>>({});
const result = ref<any>(null);
const submitted = ref(false);

async function load() {
  try {
    const r = await api("GET", "/benchmarks");
    banks.value = r.benchmarks.filter((b: any) => b.status === "online" && b.case_count > 0);
  } catch (e: any) { err.value = e.message; }
}
onMounted(load);

const publicBanks = computed(() => banks.value.filter((b) => b.visibility === "public"));
const privateBanks = computed(() => banks.value.filter((b) => b.visibility === "private"));
const groups = computed(() => {
  const byCat: Record<string, any[]> = {};
  for (const b of publicBanks.value) {
    const k = b.category || "misc";
    (byCat[k] = byCat[k] || []).push(b);
  }
  return byCat;
});

function setGroup(list: any[], on: boolean) {
  for (const b of list) selected.value[b.name] = on;
}
const chosen = computed(() => banks.value.filter((b) => selected.value[b.name]));
const chosenCount = computed(() => chosen.value.reduce((s, b) => s + b.case_count, 0));

async function submit() {
  err.value = "";
  if (!targetUrl.value.trim()) { err.value = t("runCreate.needTarget"); return; }
  if (!chosen.value.length) { err.value = t("runCreate.needBank"); return; }
  const body: any = {
    task_name: taskName.value.trim(),
    banks: chosen.value.map((b) => ({ bank: b.name, levels: levels.value })),
    target_url: targetUrl.value.trim(),
    proxy_enabled: proxy.value,
  };
  const ids = caseIds.value.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean);
  if (ids.length) body.case_ids = ids;
  if (stability.value) body.stability_k = Math.max(2, Math.min(5, stabilityK.value | 0));
  body.ai_assist = aiAssist.value;
  if (scheduled.value) body.scheduled_for = new Date(scheduled.value).toISOString().slice(0, 19);
  try {
    result.value = await api("POST", "/runs", body);
    submitted.value = true;
  } catch (e: any) { err.value = e.message; }
}
</script>

<template>
  <h2 class="page-title">{{ $t("runCreate.title") }}</h2>
  <div v-if="err" class="error-box">{{ err }}</div>

  <div class="card" v-if="submitted && result">
    <div class="ok-box">
      {{ $t("runCreate.submitted", { name: result.name, n: result.total_cases, sec: result.est_duration_s, pos: result.queue_position }) }}
    </div>
    <router-link class="btn primary" :to="'/runs/' + result.id">{{ $t("runCreate.goRun") }}</router-link>
  </div>

  <template v-else>
    <div class="inline" style="margin-bottom: 8px">
      <h2 class="page-title" style="margin: 0">{{ $t("runCreate.title") }}</h2>
      <a :href="guideUrl('run-eval')" target="_blank" class="muted">{{ $t("runCreate.guideLink") }}</a>
    </div>
    <div class="card">
      <div class="form-row"><label>{{ $t("runCreate.taskName") }}</label><input v-model="taskName" /></div>
      <div class="form-row"><label>{{ $t("runCreate.targetUrl") }}</label>
        <input v-model="targetUrl" class="mono" placeholder="http://127.0.0.1:8200" /></div>
      <div class="muted" style="margin: -4px 0 8px">{{ $t("runCreate.agentHint") }}</div>
      <label class="checkbar" style="margin: 4px 0">
        <input type="checkbox" v-model="proxy" /> {{ $t("runCreate.proxy") }}</label>
      <div class="muted" style="margin: -2px 0 8px; padding-left: 22px">{{ $t("runCreate.proxyHint") }}</div>
      <label class="checkbar" style="margin: 4px 0">
        <input type="checkbox" v-model="stability" /> {{ $t("runCreate.stability") }}</label>
        <label style="margin-left: 12px"><input type="checkbox" v-model="aiAssist" />
          ✨ {{ $t("runCreate.aiAssist") }}</label>
      <div class="inline" v-if="stability" style="margin: 2px 0 6px">
        <span class="muted">{{ $t("runCreate.stabilityK") }}</span>
        <input type="number" v-model="stabilityK" min="2" max="5" class="mono"
               style="width: 70px" />
        <span class="muted">{{ $t("runCreate.stabilityHint") }}</span>
      </div>
    </div>

    <div class="card">
      <h4>{{ $t("runCreate.banks") }}（{{ $t("runCreate.selected") }}: {{ chosen.length }}）· {{ $t("runCreate.levels") }}</h4>
      <div class="checkbar" style="margin-bottom: 10px">
        <span class="muted">{{ $t("runCreate.levels") }}:</span>
        <label v-for="l in ['L0', 'L1', 'L2']" :key="l">
          <input type="checkbox" :value="l" v-model="levels" /> {{ l }}</label>
        <span class="muted">· {{ chosenCount }} 题</span>
      </div>

      <table class="tbl">
        <tbody>
          <template v-for="(list, cat) in groups" :key="cat">
            <tr class="group-head">
              <td colspan="3">
                <label class="checkbar">
                  <input type="checkbox"
                         :checked="list.every((b: any) => selected[b.name])"
                         @change="setGroup(list, ($event.target as HTMLInputElement).checked)" />
                  {{ $t("runCreate.publicGroup") }} · {{ cat }}
                </label>
              </td>
            </tr>
            <tr v-for="b in list" :key="b.name">
              <td style="width: 30px"><input type="checkbox" v-model="selected[b.name]" /></td>
              <td><b>{{ b.display_name || b.name }}</b>
                <span v-if="(b.display_name || b.name) !== b.name" class="mono muted">{{ b.name }}</span>
                <div class="muted">{{ $i18n.locale === 'en' ? b.description_en : b.description_zh }}</div></td>
              <td class="muted">{{ b.case_count }} 题</td>
            </tr>
          </template>
          <tr v-if="privateBanks.length" class="group-head">
            <td colspan="3">
              <label class="checkbar">
                <input type="checkbox"
                       :checked="privateBanks.every((b: any) => selected[b.name])"
                       @change="setGroup(privateBanks, ($event.target as HTMLInputElement).checked)" />
                {{ $t("runCreate.privateGroup") }}
              </label>
            </td>
          </tr>
          <tr v-for="b in privateBanks" :key="b.name">
            <td style="width: 30px"><input type="checkbox" v-model="selected[b.name]" /></td>
            <td><b>{{ b.display_name || b.name }}</b> <span class="mono muted">{{ b.name }}</span></td>
            <td class="muted">{{ b.case_count }} 题</td>
          </tr>
        </tbody>
      </table>
      <div v-if="!banks.length" class="muted">{{ $t("runCreate.noBanks") }}</div>
    </div>

    <div class="card">
      <div class="form-row"><label>{{ $t("runCreate.caseIds") }}</label>
        <textarea v-model="caseIds" class="mono" /></div>
      <div class="form-row"><label>{{ $t("runCreate.schedule") }}</label>
        <input type="datetime-local" v-model="scheduled" /></div>
      <button class="btn primary" @click="submit">{{ $t("runCreate.submit") }}</button>
    </div>
  </template>

</template>
