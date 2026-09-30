<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import UserCenter from "./components/UserCenter.vue";
import { setLang } from "./i18n";
import { api, clearAuth, store } from "./store";

const { t, locale } = useI18n();
const router = useRouter();
const showUc = ref(false);
const ucStartTab = ref<"profile" | "ai" | "users" | "settings">("profile");

// first-login AI-assist prompt: only for users with no AI config who have not
// dismissed it before (configured users never see it)
const showAiPrompt = ref(false);
onMounted(async () => {
  if (!store.token) return;
  try {
    const me = await api("GET", "/auth/me");
    store.user = { username: me.username, role: me.role,
                   display_name: me.display_name || "",
                   ai_configured: me.ai_configured,
                   ai_prompt_dismissed: me.ai_prompt_dismissed };
    showAiPrompt.value = !me.ai_configured && !me.ai_prompt_dismissed;
  } catch { /* expired token: the next guarded view bounces to login */ }
});

function configureAi() {
  showAiPrompt.value = false;
  ucStartTab.value = "ai";
  showUc.value = true;
}

async function dismissAiPrompt() {
  showAiPrompt.value = false;
  try { await api("POST", "/auth/me/ai-prompt-dismissed"); } catch { /* non-fatal */ }
}
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
  showAiPrompt.value = false;
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

  <dialog :open="showAiPrompt" class="uc-dialog" style="min-width: 420px; max-width: 480px">
    <h4>{{ $t("aiPrompt.title") }}</h4>
    <p class="muted" style="line-height: 1.8">{{ $t("aiPrompt.text") }}</p>
    <div class="inline" style="justify-content: flex-end; margin-top: 10px">
      <button class="btn" @click="dismissAiPrompt">{{ $t("aiPrompt.later") }}</button>
      <button class="btn primary" @click="configureAi">{{ $t("aiPrompt.configure") }}</button>
    </div>
  </dialog>

  <UserCenter :open="showUc" :start-tab="ucStartTab" @close="showUc = false" />

  <main class="page">
    <router-view />
  </main>
</template>
