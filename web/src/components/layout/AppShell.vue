<script setup lang="ts">
import { RouterLink } from "vue-router";

import ConversationListPanel from "@/components/layout/ConversationListPanel.vue";

const nav = [
  {
    to: "/chat",
    title: "對話",
    desc: "提出問題並查看合約分析回應。",
  },
  {
    to: "/upload",
    title: "上傳檔案",
    desc: "上傳文件並寫入知識庫切片。",
  },
  {
    to: "/sources",
    title: "知識來源",
    desc: "查看已建立索引的檔案與切片數量。",
  },
  {
    to: "/admin",
    title: "管理後台",
    desc: "查看服務狀態、模型、上傳工具與 EVAL。",
  },
] as const;
</script>

<template>
  <div class="shell">
    <aside class="sidebar" aria-label="主要導覽">
      <div class="brand">
        <p class="brand-title font-display">
          合約法遵助理
        </p>
      </div>
      <ConversationListPanel />
      <p class="nav-section-label">
        功能選單
      </p>
      <nav class="nav" aria-label="頁面導覽">
        <RouterLink
          v-for="item in nav"
          :key="item.to"
          :to="item.to"
          class="nav-link"
        >
          <span class="nav-title">{{ item.title }}</span>
          <span class="nav-desc">{{ item.desc }}</span>
        </RouterLink>
      </nav>
    </aside>
    <main class="main">
      <div class="main-inner">
        <slot />
      </div>
    </main>
  </div>
</template>

<style scoped>
.shell {
  display: flex;
  min-height: 100vh;
  min-height: 100dvh;
  width: 100%;
  max-width: 1400px;
  margin: 0 auto;
}

.sidebar {
  width: var(--sidebar-width);
  flex-shrink: 0;
  padding: var(--space-3) var(--space-2);
  background: var(--color-bg-surface);
  border-right: 1px solid var(--color-border-subtle);
  box-shadow: var(--shadow-sm);
}

.brand {
  padding-bottom: var(--space-3);
  margin-bottom: var(--space-2);
  border-bottom: 1px solid var(--color-border-subtle);
}

.brand-title {
  font-family: var(--font-display);
  font-weight: 700;
  letter-spacing: -0.02em;
  font-size: clamp(1.25rem, 2.8vw, 1.75rem);
  line-height: 1.28;
  margin: 0;
  color: var(--color-text-primary);
}

.nav-section-label {
  margin: 0 0 var(--space-2);
  font-size: var(--text-caption-size);
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--color-text-muted);
}

.nav {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.nav-link {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  padding: var(--space-2) var(--space-2);
  border-radius: var(--radius-md);
  border: 1px solid transparent;
  color: var(--color-text-secondary);
  text-decoration: none;
  font-size: var(--text-body-sm-size);
  line-height: 1.35;
  transition:
    background-color 0.15s ease,
    border-color 0.15s ease,
    color 0.15s ease;
}

.nav-title {
  font-size: var(--text-body-size);
  font-weight: 600;
  color: var(--color-text-primary);
}

.nav-desc {
  font-size: var(--text-caption-size);
  line-height: var(--text-caption-leading);
  color: var(--color-text-muted);
}

.nav-link:hover {
  background: var(--color-bg-muted);
  border-color: var(--color-border-subtle);
}

.nav-link.router-link-active {
  background: var(--color-accent-muted);
  border-color: var(--color-border-strong);
  color: var(--color-text-primary);
}

.nav-link.router-link-active .nav-title {
  color: var(--color-accent);
}

.nav-link.router-link-active .nav-desc {
  color: var(--color-text-secondary);
}

.nav-link:focus-visible {
  outline: var(--focus-ring);
  outline-offset: var(--focus-ring-offset);
}

.main {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: var(--space-5) var(--space-4);
}

.main-inner {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  max-width: min(1100px, 100%);
  width: 100%;
  margin: 0 auto;
}

@media (max-width: 720px) {
  .shell {
    flex-direction: column;
    max-width: none;
  }

  .sidebar {
    width: 100%;
    border-right: none;
    border-bottom: 1px solid var(--color-border-subtle);
    max-height: min(70vh, 520px);
    overflow-y: auto;
  }

  .nav {
    flex-direction: row;
    flex-wrap: wrap;
  }

  .nav-link {
    flex: 1 1 160px;
    min-width: 140px;
  }

  .main {
    flex: 1;
    min-height: 0;
    padding: var(--space-4) var(--space-3);
  }
}
</style>
