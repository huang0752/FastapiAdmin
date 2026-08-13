<template>
  <div aria-hidden="true" class="brand-motion-layer" :class="{ 'is-paused': paused }"><slot /></div>
</template>
<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
defineOptions({ name: "BrandMotionLayer" });
const paused = ref(false);
const sync = () => (paused.value = document.visibilityState === "hidden");
onMounted(() => {
  sync();
  document.addEventListener("visibilitychange", sync);
});
onBeforeUnmount(() => document.removeEventListener("visibilitychange", sync));
</script>
