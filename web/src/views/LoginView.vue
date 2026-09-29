<script setup lang="ts">
import { ref } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import { api, setAuth, setLang } from "../store";

const { t, locale } = useI18n();
const router = useRouter();
const mode = ref<"login" | "register">("login");
const username = ref("");
const password = ref("");
const display = ref("");
const err = ref("");

function toggleLang() { setLang(locale.value === "zh" ? "en" : "zh"); }

async function submit() {
  err.value = "";
  try {
    if (mode.value === "login") {
      const r = await api("POST", "/auth/login", { username: username.value, password: password.value });
      setAuth(r.token, { username: r.username, role: r.role, display_name: r.display_name || "" });
      router.push("/banks");
    } else {
      await api("POST", "/auth/register", {
        username: username.value, password: password.value, display_name: display.value });
      mode.value = "login";
      err.value = t("login.registerOk");
    }
  } catch (e: any) {
    err.value = e.message;
  }
}
</script>

<template>
  <div style="max-width: 400px; margin: 8vh auto 0" class="card">
    <div class="inline" style="justify-content: space-between; margin-bottom: 12px">
      <h2 style="margin: 0">{{ $t("login.title") }}</h2>
      <button class="btn small" @click="toggleLang">{{ $t("nav.language") }}</button>
    </div>
    <div class="checkbar" style="margin-bottom: 12px">
      <button class="btn small" :class="{ primary: mode === 'login' }" @click="mode = 'login'">{{ $t("login.login") }}</button>
      <button class="btn small" :class="{ primary: mode === 'register' }" @click="mode = 'register'">{{ $t("login.register") }}</button>
    </div>
    <form @submit.prevent="submit">
      <div class="form-row"><label>{{ $t("login.username") }}</label>
        <input v-model="username" autocomplete="username" /></div>
      <div class="form-row"><label>{{ $t("login.password") }}</label>
        <input type="password" v-model="password"
               :autocomplete="mode === 'login' ? 'current-password' : 'new-password'" /></div>
      <div v-if="mode === 'register'" class="form-row"><label>{{ $t("login.display") }}</label>
        <input v-model="display" /></div>
      <div v-if="err" class="error-box">{{ err }}</div>
      <div v-if="mode === 'register'" class="muted" style="margin: 6px 0">{{ $t("login.registerHint") }}</div>
      <button class="btn primary" type="submit" style="width: 100%; margin-top: 6px">
        {{ mode === "login" ? $t("login.login") : $t("login.register") }}
      </button>
    </form>
  </div>
</template>
