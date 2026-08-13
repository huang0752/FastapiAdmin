<template>
  <BrandMotionLayer><component :is="sceneComponent" /></BrandMotionLayer>
</template>
<script setup lang="ts">
import { computed } from "vue";
import type { ProductBrandScene } from "@/config/brand/loginBrandPoster";
import BrandMotionLayer from "./BrandMotionLayer.vue";
import TraceBrandScene from "./scenes/TraceBrandScene.vue";
import AgriBrandScene from "./scenes/AgriBrandScene.vue";
import LogisticBrandScene from "./scenes/LogisticBrandScene.vue";
defineOptions({ name: "LoginBrandScene" });
const props = defineProps<{ scene: ProductBrandScene }>();
const scenes = {
  trace: TraceBrandScene,
  agri: AgriBrandScene,
  logistic: LogisticBrandScene,
} as const;
const sceneComponent = computed(() => scenes[props.scene]);
</script>
