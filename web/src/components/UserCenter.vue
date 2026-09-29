<script setup lang="ts">
// User center dialog (W6-19): account info + password change for everyone; user management
// (admins) and platform settings (owner) move here from the old admin page.
import { ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import Sel from "./Sel.vue";
import { api, fmtTime, hasMin, store } from "../store";

const { t, locale } = useI18n();
const props = defineProps<{ open: boolean }>();
const emit = defineEmits<{ (e: "close"): void }>();

const tab = ref<"profile" | "ai" | "users" | "settings">("profile");
const users = ref<any[]>([]);
const err = ref("");
const msg = ref("");
const oldPw = ref("");
const newPw = ref("");
const isOwner = store.user?.role === "owner";
const newUser = ref<any>({ username: "", password: "", role: "member", display_name: "" });
const settings = ref<any>(null);
const me = ref<any>(null);
const ai = ref<any>({ base_url: "", api_key: "", model: "", judge_auto: false });
const roleOptions = isOwner
  ? [{ value: "member", label: "member" }, { value: "admin", label: "admin" },
     { value: "viewer", label: "viewer" }]
  : [{ value: "member", label: "member" }];
const regOptions = [
  { value: "true", label: t("common.yes") }, { value: "false", label: t("common.no") }];

watch(() => props.open, (v) => {
  if (v) loadMe();                       // refresh account info each time it opens
});

async function loadMe() {
  err.value = "";
  try {
    me.value = await api("GET", "/auth/me");
    ai.value = { base_url: me.value.ai?.base_url || "", api_key: "",
                 model: me.value.ai?.model || "",
                 judge_auto: !!me.value.ai?.judge_auto };
  }
  catch (e: any) { err.value = e.message; }
}
async function saveAi() {
  err.value = ""; msg.value = "";
  try {
    const r = await api("PUT", "/auth/me/ai-settings", ai.value);
    msg.value = t("uc.aiSaved") + (r.ai_configured ? "" : " · " + t("uc.aiNotConfigured"));
    await loadMe();
  } catch (e: any) { err.value = e.message; }
}
async function loadUsers() {
  err.value = "";
  try { users.value = (await api("GET", "/admin/users")).users; }
  catch (e: any) { err.value = e.message; }
}
async function loadSettings() {
  err.value = "";
  try { settings.value = await api("GET", "/admin/settings"); }
  catch (e: any) { err.value = e.message; }
}
function openTab(name: any) {
  tab.value = name;
  if (name === "users") loadUsers();
  if (name === "settings") loadSettings();
  if (name === "ai") loadPrompts();
}

// ---- AI prompt registry (per-user overrides; empty text = builtin default) ----
const aiPrompts = ref<any[]>([]);
const editing = ref("");
const draftPrompt = ref("");
async function loadPrompts() {
  try { aiPrompts.value = (await api("GET", "/auth/me/ai-prompts")).prompts; }
  catch (e: any) { err.value = e.message; }
}
function editPrompt(p: any) {
  editing.value = p.name;
  draftPrompt.value = p.overridden ? p.current : p.default;
}
async function savePrompt(p: any) {
  err.value = ""; msg.value = "";
  try {
    await api("PUT", "/auth/me/ai-prompts", { name: p.name, text: draftPrompt.value });
    msg.value = p.title + " ✓";
    editing.value = "";
    await loadPrompts();
  } catch (e: any) { err.value = e.message; }
}
async function resetPrompt(p: any) {
  err.value = ""; msg.value = "";
  try {
    await api("PUT", "/auth/me/ai-prompts", { name: p.name, text: "" });
    msg.value = p.title + " ✓";
    await loadPrompts();
  } catch (e: any) { err.value = e.message; }
}

async function submitPw() {
  msg.value = ""; err.value = "";
  try {
    await api("PATCH", "/auth/password", { old_password: oldPw.value, new_password: newPw.value });
    msg.value = t("login.pwChanged");
    oldPw.value = ""; newPw.value = "";
  } catch (e: any) { err.value = e.message; }
}
async function createUser() {
  err.value = ""; msg.value = "";
  try {
    await api("POST", "/admin/users", newUser.value);
    newUser.value = { username: "", password: "", role: "member", display_name: "" };
    await loadUsers();
  } catch (e: any) { err.value = e.message; }
}
async function resetPw(u: any) {
  const pw = prompt(t("admin.newPw"));
  if (!pw) return;
  try { await api("PATCH", "/admin/users/" + u.username, { new_password: pw }); msg.value = u.username + " ✓"; }
  catch (e: any) { err.value = e.message; }
}
async function setRole(u: any, role: string) {
  try { await api("PATCH", "/admin/users/" + u.username, { role }); await loadUsers(); }
  catch (e: any) { err.value = e.message; }
}
async function removeUser(u: any) {
  if (!confirm(t("common.confirmDelete"))) return;
  try { await api("DELETE", "/admin/users/" + u.username); await loadUsers(); }
  catch (e: any) { err.value = e.message; }
}
async function saveSettings() {
  err.value = ""; msg.value = "";
  try {
    await api("PUT", "/admin/settings", {
      proxy_upstream: settings.value.proxy_upstream,
      proxy_agent_url: settings.value.proxy_agent_url,
      report_retention_days: parseInt(settings.value.report_retention_days) || 30,
      registration_open: settings.value.registration_open === true
        || settings.value.registration_open === "true",
    });
    msg.value = t("admin.settingsSaved");
  } catch (e: any) { err.value = e.message; }
}
</script>

<template>
  <div>
  <dialog :open="open" class="uc-dialog" style="min-width: 520px; max-width: 560px">
    <div class="inline" style="margin-bottom: 10px">
      <h3 style="margin: 0">{{ $t("uc.title") }}</h3>
      <span class="spacer" style="flex:1"></span>
      <button class="btn small" @click="emit('close')">{{ $t("common.close") }}</button>
    </div>
    <div v-if="me" class="card" style="padding: 10px 14px">
      <table class="tbl" style="width: auto">
        <tbody>
          <tr><td class="muted">{{ $t("admin.colUser") }}</td>
              <td class="mono"><b>{{ me.username }}</b></td>
              <td class="muted">{{ $t("admin.colRole") }}</td>
              <td><span class="chip public">{{ $t("role." + me.role) }}</span></td></tr>
          <tr><td class="muted">{{ $t("admin.colDisplay") }}</td><td>{{ me.display_name || "-" }}</td>
              <td class="muted">{{ $t("admin.colCreated") }}</td>
              <td class="mono">{{ fmtTime(me.created_at) }}</td></tr>
          <tr><td class="muted">{{ $t("admin.colLogin") }}</td>
              <td class="mono">{{ fmtTime(me.last_login_at) }}</td><td colspan="2"></td></tr>
        </tbody>
      </table>
    </div>
    <div class="checkbar" style="margin: 10px 0">
      <button class="btn small" :class="{ primary: tab === 'profile' }" @click="openTab('profile')">
        {{ $t("uc.profile") }} / {{ $t("uc.changePassword") }}</button>
      <button class="btn small" :class="{ primary: tab === 'ai' }" @click="openTab('ai')">
        ✨ {{ $t("uc.aiSettings") }}</button>
      <button v-if="hasMin(store.user?.role, 'admin')" class="btn small"
              :class="{ primary: tab === 'users' }" @click="openTab('users')">
        {{ $t("uc.userManage") }}</button>
      <button v-if="isOwner" class="btn small" :class="{ primary: tab === 'settings' }"
              @click="openTab('settings')">{{ $t("uc.platformSettings") }}</button>
    </div>
    <div v-if="msg" class="ok-box">{{ msg }}</div>
    <div v-if="err" class="error-box">{{ err }}</div>

    <div v-if="tab === 'profile'">
      <template v-if="store.user?.role === 'owner'">
        <div class="ok-box">{{ $t("login.ownerHint") }}</div>
      </template>
      <template v-else>
        <div class="form-row"><label>{{ $t("login.oldPassword") }}</label>
          <input type="password" v-model="oldPw" :placeholder="$t('login.oldPassword')" /></div>
        <div class="form-row"><label>{{ $t("login.newPassword") }}</label>
          <input type="password" v-model="newPw" :placeholder="$t('login.newPassword')" /></div>
        <button class="btn primary" @click="submitPw">{{ $t("common.save") }}</button>
      </template>
    </div>

    <div v-if="tab === 'ai'">
      <div class="muted" style="margin-bottom: 8px">{{ $t("uc.aiHint") }}</div>
      <div class="form-row"><label>{{ $t("uc.aiBaseUrl") }}</label>
        <input v-model="ai.base_url" class="mono" placeholder="https://<gateway>/v1" /></div>
      <div class="form-row"><label>{{ $t("uc.aiApiKey") }}</label>
        <input v-model="ai.api_key" type="password" class="mono"
               :placeholder="me?.ai_configured ? '········（' + $t('uc.aiKeyKeep') + '）' : 'sk-...'" /></div>
      <div class="form-grid">
        <div class="form-row"><label>{{ $t("uc.aiModel") }}</label>
          <input v-model="ai.model" class="mono" placeholder="GLM5.3-Flash" /></div>
        <div class="form-row"><label>{{ $t("uc.aiJudgeAuto") }}</label>
          <Sel v-model="ai.judge_auto" :options="[
            { value: false, label: t('common.no') }, { value: true, label: t('common.yes') }]" /></div>
      </div>
      <div class="muted">{{ $t("uc.aiJudgeAutoHint") }}</div>
      <div class="muted" style="margin-top: 4px">
        {{ $t("uc.aiStatus") }}:
        <span class="chip" :class="me?.ai_configured ? 'PASS' : 'PENDING'">
          {{ me?.ai_configured ? $t("uc.aiOn") : $t("uc.aiOff") }}</span>
      </div>
      <button class="btn primary" style="margin-top: 8px" @click="saveAi">{{ $t("common.save") }}</button>

      <!-- AI prompt registry: every LLM prompt the platform ships, editable per user -->
      <div style="border-top: 1px solid #e2e8f0; margin-top: 14px; padding-top: 10px">
        <div class="inline" style="margin-bottom: 6px">
          <b>✨ {{ $t("uc.aiPrompts") }}</b>
          <span class="muted" style="font-size: 12px">{{ $t("uc.aiPromptsHint") }}</span>
        </div>
        <div v-for="p in aiPrompts" :key="p.name" style="margin-bottom: 8px">
          <div class="inline">
            <b style="min-width: 140px">{{ locale === "en" ? (p.title_en || p.title) : p.title }}</b>
            <span v-if="p.overridden" class="chip PENDING">{{ $t("uc.aiPromptCustom") }}</span>
            <span class="spacer" style="flex:1"></span>
            <button class="btn small" @click="editPrompt(p)">{{ $t("common.edit") }}</button>
            <button v-if="p.overridden" class="btn small" @click="resetPrompt(p)">
              {{ $t("uc.aiPromptReset") }}</button>
          </div>
          <div class="muted" style="font-size: 12px; margin: 2px 0">{{ locale === "en" ? (p.desc_en || p.desc) : p.desc }}</div>
          <textarea v-if="editing === p.name" v-model="draftPrompt" rows="8"
                    style="width: 100%; font-family: monospace; font-size: 12px" />
          <template v-if="editing === p.name">
            <button class="btn small primary" style="margin-top: 4px" @click="savePrompt(p)">
              {{ $t("common.save") }}</button>
            <button class="btn small" style="margin-top: 4px" @click="editing = ''">
              {{ $t("common.close") }}</button>
          </template>
        </div>
      </div>
    </div>

    <div v-if="tab === 'users'">
      <div class="form-grid" style="margin-bottom: 10px">
        <div class="form-row"><label>{{ $t("login.username") }}</label><input v-model="newUser.username" class="mono" /></div>
        <div class="form-row"><label>{{ $t("login.password") }}</label><input v-model="newUser.password" type="password" /></div>
        <div class="form-row"><label>{{ $t("admin.colRole") }}</label>
          <Sel v-model="newUser.role" :options="roleOptions" /></div>
        <div class="form-row"><label>{{ $t("admin.colDisplay") }}</label><input v-model="newUser.display_name" /></div>
      </div>
      <button class="btn primary" @click="createUser">{{ $t("admin.createUser") }}</button>
      <div class="muted" style="margin-top: 6px" v-if="!isOwner">{{ $t("admin.memberOnly") }}</div>
      <div class="tbl-wrap" style="margin-top: 12px">
      <table class="tbl">
        <thead><tr>
          <th>{{ $t("admin.colUser") }}</th><th>{{ $t("admin.colDisplay") }}</th>
          <th>{{ $t("admin.colRole") }}</th><th>{{ $t("admin.colCreated") }}</th>
          <th>{{ $t("admin.colLogin") }}</th><th>{{ $t("common.actions") }}</th>
        </tr></thead>
        <tbody>
          <tr v-for="u in users" :key="u.username">
            <td class="mono">{{ u.username }}</td>
            <td>{{ u.display_name || "-" }}</td>
            <td><span class="chip public">{{ $t("role." + u.role) }}</span></td>
            <td class="muted mono">{{ u.created_at }}</td>
            <td class="muted mono">{{ u.last_login_at || "-" }}</td>
            <td style="white-space: nowrap">
              <template v-if="u.role !== 'owner'">
                <button class="btn small" @click="resetPw(u)">{{ $t("admin.resetPw") }}</button>
                <template v-if="isOwner">
                  <button v-if="u.role === 'member'" class="btn small" @click="setRole(u, 'admin')">{{ $t("admin.promote") }}</button>
                  <button v-if="u.role === 'admin'" class="btn small" @click="setRole(u, 'member')">{{ $t("admin.demote") }}</button>
                </template>
                <button class="btn small danger" @click="removeUser(u)">{{ $t("common.delete") }}</button>
              </template>
              <span v-else class="muted">owner</span>
            </td>
          </tr>
        </tbody>
      </table>
      </div>
    </div>

    <div v-if="tab === 'settings' && settings">
      <div class="form-row"><label>{{ $t("admin.proxyUpstream") }}</label>
        <input v-model="settings.proxy_upstream" class="mono" placeholder="https://<gateway>/v1" /></div>
      <div class="form-row"><label>{{ $t("admin.proxyAgentUrl") }}</label>
        <input v-model="settings.proxy_agent_url" class="mono" placeholder="http://172.17.0.1:8300/v1" /></div>
      <div class="form-grid">
        <div class="form-row"><label>{{ $t("admin.retention") }}</label>
          <input v-model="settings.report_retention_days" type="number" min="1" /></div>
        <div class="form-row"><label>{{ $t("admin.registration") }}</label>
          <Sel v-model="settings.registration_open" :options="regOptions" /></div>
      </div>
      <div class="muted">{{ $t("admin.ports", { r: settings.receiver_port, p: settings.proxy_port }) }}</div>
      <button class="btn primary" style="margin-top: 8px" @click="saveSettings">{{ $t("common.save") }}</button>
    </div>
  </dialog>
  </div>
</template>
