import { createRouter, createWebHistory } from "vue-router";

import AdminView from "../views/AdminView.vue";
import ChatView from "../views/ChatView.vue";
import EvalView from "../views/EvalView.vue";
import SourcesView from "../views/SourcesView.vue";
import UploadView from "../views/UploadView.vue";

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: "/", redirect: "/chat" },
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
    {
      path: "/admin",
      name: "admin",
      component: AdminView,
    },
    {
      path: "/eval",
      name: "eval",
      component: EvalView,
    },
  ],
});

export default router;
