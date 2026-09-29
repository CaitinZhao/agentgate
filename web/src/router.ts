import { createRouter, createWebHashHistory } from "vue-router";
import { store } from "./store";

// Hash history: the built dist is served by FastAPI StaticFiles, which has no SPA
// history fallback — hash URLs keep deep links working without server config.
export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/login", component: () => import("./views/LoginView.vue") },
    { path: "/", redirect: "/banks" },
    { path: "/banks", component: () => import("./views/BenchmarksView.vue") },
    { path: "/banks/new", component: () => import("./views/BankCreateView.vue") },
    { path: "/banks/:name/overview", component: () => import("./views/BankOverviewView.vue"), props: true },
    { path: "/banks/:name/edit", component: () => import("./views/BankEditView.vue"), props: true },
    { path: "/banks/:name", component: () => import("./views/BankDetailView.vue"), props: true },
    { path: "/runs", component: () => import("./views/RunsView.vue") },
    { path: "/runs/new", component: () => import("./views/RunCreateView.vue") },
    { path: "/runs/:id", component: () => import("./views/RunDetailView.vue") },
    { path: "/reports", redirect: "/runs" },       // merged into the runs page (W6-18)
    { path: "/admin", redirect: "/banks" },         // merged into the user center (W6-19)
    { path: "/compare", component: () => import("./views/CompareView.vue") },
  ],
});

router.beforeEach((to) => {
  if (!store.token && to.path !== "/login") return "/login";
  if (store.token && to.path === "/login") return "/banks";
  return true;
});
