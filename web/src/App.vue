<script setup lang="ts">
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import UserCenter from "./components/UserCenter.vue";
import { setLang } from "./i18n";
import { clearAuth, store } from "./store";

const { t, locale } = useI18n();
const router = useRouter();
const showUc = ref(false);
// the user-center dialog must not survive navigation (it is mounted app-wide)
router.afterEach(() => { showUc.value = false; });

const roleLabel = computed(() =>
  store.user ? t("role." + store.user.role) : "");

function toggleLang() {
  setLang(locale.value === "zh" ? "en" : "zh");
}

function logout() {
  apiLogout();
  clearAuth();
  router.push("/login");
}

async function apiLogout() {
  try {
    const token = localStorage.getItem("ag_token") || "";
    await fetch("/api/v1/auth/logout", {
      method: "POST", headers: { Authorization: "Bearer " + token } });
  } catch { /* best-effort */ }
}
</script>

<template>
  <header class="topbar" v-if="$route.path !== '/login'">
    <span class="brand">{{ $t("common.appName") }}</span>
    <nav>
      <router-link to="/banks">{{ $t("nav.benchmarks") }}</router-link>
      <router-link to="/runs">{{ $t("nav.runs") }}</router-link>
    </nav>
    <span class="spacer"></span>
    <button class="lang-btn" @click="toggleLang">{{ $t("nav.language") }}</button>
    <button class="user-btn" @click="showUc = true">
      {{ store.user?.username }} · {{ roleLabel }}
    </button>
    <button class="user-btn" @click="logout">{{ $t("nav.logout") }}</button>
  </header>

  <UserCenter :open="showUc" @close="showUc = false" />

  <main class="page">
    <router-view />
  </main>
</template>
