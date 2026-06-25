<script setup lang="ts">
</script>

<template>
  <aside class="app-sidebar">
    <div class="sidebar-brand">
      <h1>FSPEF-VERS</h1>
      <p>频率共振直接勘探分析系统</p>
    </div>
    <nav class="sidebar-nav">
      <router-link to="/" class="nav-item"><span class="icon">📊</span> 仪表盘</router-link>
      <router-link to="/analysis" class="nav-item"><span class="icon">🔬</span> 新建分析</router-link>
      <router-link to="/library" class="nav-item"><span class="icon">📚</span> 光谱库</router-link>
    </nav>
    <div style="padding: 12px 16px; border-top: 1px solid var(--border);">
      <div style="font-size: 11px; color: var(--text-dim); line-height: 1.5;">
        基于 Yakymchuk 频率共振理论<br>
        H = C / (2f)
      </div>
    </div>
  </aside>
  <main class="app-main">
    <router-view />
  </main>
</template>
