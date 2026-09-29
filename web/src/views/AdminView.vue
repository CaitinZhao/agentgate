<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import Sel from "../components/Sel.vue";
import { api, hasMin, store } from "../store";

const { t } = useI18n();
const users = ref<any[]>([]);
const err = ref("");
const msg = ref("");
const isOwner = hasMin(store.user?.role, "owner") && store.user?.role === "owner";
const newUser = ref<any>({ username: "", password: "", role: "member", display_name: "" });
const settings = ref<any>(null);
const roleOptions = isOwner
  ? [{ value: "member", label: "member" }, { value: "admin", label: "admin" },
     { value: "viewer", label: "viewer" }]
  : [{ value: "member", label: "member" }];
const regOptions = [
  { value: "true", label: t("common.yes") }, { value: "false", label: t("common.no") }];

async function load() {
  err.value = "";
  try {
    users.value = (await api("GET", "/admin/users")).users;
    if (isOwner) settings.value = await api("GET", "/admin/settings");
  } catch (e: any) { err.value = e.message; }
}
onMounted(load);

async function createUser() {
  err.value = ""; msg.value = "";
  try {
    await api("POST", "/admin/users", newUser.value);
    newUser.value = { username: "", password: "", role: "member", display_name: "" };
    await load();
  } catch (e: any) { err.value = e.message; }
}

async function resetPw(u: any) {
  const pw = prompt(t("admin.newPw"));
  if (!pw) return;
  try {
    await api("PATCH", "/admin/users/" + u.username, { new_password: pw });
    msg.value = u.username + " ✓";
  } catch (e: any) { err.value = e.message; }
}

async function setRole(u: any, role: string) {
  try {
    await api("PATCH", "/admin/users/" + u.username, { role });
    await load();
  } catch (e: any) { err.value = e.message; }
}

async function removeUser(u: any) {
  if (!confirm(t("common.confirmDelete"))) return;
  try {
    await api("DELETE", "/admin/users/" + u.username);
    await load();
  } catch (e: any) { err.value = e.message; }
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
  <h2 class="page-title">{{ $t("admin.title") }}</h2>
  <div v-if="err" class="error-box">{{ err }}</div>
  <div v-if="msg" class="ok-box">{{ msg }}</div>

  <div class="card">
    <h4>{{ $t("admin.users") }}</h4>
    <div class="form-grid" style="margin-bottom: 10px">
      <div class="form-row"><label>{{ $t("login.username") }}</label><input v-model="newUser.username" class="mono" /></div>
      <div class="form-row"><label>{{ $t("login.password") }}</label><input v-model="newUser.password" type="password" /></div>
      <div class="form-row"><label>{{ $t("admin.colRole") }}</label>
        <Sel v-model="newUser.role" :options="roleOptions" /></div>
      <div class="form-row"><label>{{ $t("admin.colDisplay") }}</label><input v-model="newUser.display_name" /></div>
    </div>
    <button class="btn primary" @click="createUser">{{ $t("admin.createUser") }}</button>
    <div class="muted" style="margin-top: 6px" v-if="!isOwner">{{ $t("admin.memberOnly") }}</div>

    <table class="tbl" style="margin-top: 12px">
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

  <div class="card" v-if="isOwner && settings">
    <h4>{{ $t("admin.settings") }}</h4>
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
</template>
