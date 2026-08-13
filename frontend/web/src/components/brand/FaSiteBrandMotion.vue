<template>
  <div
    aria-hidden="true"
    class="site-brand-motion"
    :class="[`site-brand-motion--${brand.motion}`, { 'is-paused': isPaused }]"
  >
    <img class="site-brand-motion__mark" :src="brand.logo" alt="" />
    <span class="site-brand-motion__orbit" />
    <span class="site-brand-motion__signal" />
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import type { SiteBrandTheme } from "@/config/brand/siteBrandTheme";
import "@/styles/brand/site-brand-motion.scss";

defineOptions({ name: "FaSiteBrandMotion" });
defineProps<{ brand: SiteBrandTheme }>();

const isPaused = ref(false);
const syncVisibility = () => {
  isPaused.value = document.visibilityState === "hidden";
};

onMounted(() => {
  syncVisibility();
  document.addEventListener("visibilitychange", syncVisibility);
});

onBeforeUnmount(() => document.removeEventListener("visibilitychange", syncVisibility));
</script>
