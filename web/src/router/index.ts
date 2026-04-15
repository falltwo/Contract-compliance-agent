import { createRouter, createWebHistory } from "vue-router";
import type { RouteRecordRaw } from "vue-router";

import AdminView from "../views/AdminView.vue";
import ChatView from "../views/ChatView.vue";
import SourcesView from "../views/SourcesView.vue";
import UploadView from "../views/UploadView.vue";
import { IS_ADMIN_TARGET, IS_FRONTEND_TARGET } from "@/config/runtime";

const frontRoutes = [
  {
    path: "/chat",
    name: "chat",
    component: ChatView,
  },
  {
    path: "/upload",
    name: "upload",
    component: UploadView,
  },
  {
    path: "/sources",
    name: "sources",
    component: SourcesView,
  },
];

const adminRoutes = [
  {
    path: "/admin",
    name: "admin",
    component: AdminView,
  },
];

const routes: RouteRecordRaw[] = [];
if (IS_ADMIN_TARGET) {
  routes.push({ path: "/", redirect: "/admin" }, ...adminRoutes);
} else if (IS_FRONTEND_TARGET) {
  routes.push({ path: "/", redirect: "/chat" }, ...frontRoutes);
} else {
  routes.push({ path: "/", redirect: "/chat" }, ...frontRoutes, ...adminRoutes);
}

routes.push({ path: "/:pathMatch(.*)*", redirect: IS_ADMIN_TARGET ? "/admin" : "/chat" });

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
});

export default router;
