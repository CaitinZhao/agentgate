import { createI18n } from "vue-i18n";
import { en, zh } from "./messages";

// Default language follows the stored preference, then the browser.
const saved = localStorage.getItem("ag_lang");

export const i18n = createI18n({
  legacy: false,
  locale: saved || (navigator.language.startsWith("zh") ? "zh" : "en"),
  fallbackLocale: "en",
  messages: { zh, en },
});

export function setLang(lang: "zh" | "en") {
  i18n.global.locale.value = lang;
  localStorage.setItem("ag_lang", lang);
  document.documentElement.lang = lang;
}
