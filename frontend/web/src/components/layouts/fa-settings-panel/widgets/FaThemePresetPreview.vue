<template>
  <div class="theme-preset-preview">
    <div class="theme-preset-preview__sidebar" :style="{ background: tokens.sidebarBackground }">
      <span class="theme-preset-preview__logo" :style="{ background: primary }"></span>
      <span
        class="theme-preset-preview__menu is-active"
        :style="{ background: tokens.sidebarActiveBackground }"
      ></span>
      <span class="theme-preset-preview__menu" :style="{ background: tokens.sidebarText }"></span>
      <span class="theme-preset-preview__menu" :style="{ background: tokens.sidebarText }"></span>
    </div>
    <div class="theme-preset-preview__content">
      <div class="theme-preset-preview__card">
        <span class="theme-preset-preview__button" :style="{ background: primary }"></span>
        <div class="theme-preset-preview__chart">
          <span
            v-for="(color, index) in chartColors"
            :key="color"
            :style="{ background: color, height: `${45 + index * 20}%` }"
          ></span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import type { ThemePresetModeTokens } from "@/config/themePresets";

defineOptions({ name: "FaThemePresetPreview" });

const props = defineProps<{ tokens: ThemePresetModeTokens; primary: string }>();
const chartColors = computed(() => props.tokens.chartPalette.slice(0, 3));
</script>

<style scoped lang="scss">
.theme-preset-preview {
  display: flex;
  width: 100%;
  height: 100%;
  background: var(--fa-gray-200);

  &__sidebar {
    display: flex;
    flex-direction: column;
    gap: 3px;
    width: 29%;
    padding: 5px 3px;
  }

  &__logo {
    width: 10px;
    height: 10px;
    margin: 0 auto 3px;
    border-radius: 50%;
  }

  &__menu {
    width: 100%;
    height: 5px;
    border-radius: 2px;
    opacity: 0.35;

    &.is-active {
      opacity: 1;
    }
  }

  &__content {
    display: flex;
    flex: 1;
    min-width: 0;
    padding: 4px;
  }

  &__card {
    display: flex;
    flex: 1;
    flex-direction: column;
    justify-content: space-between;
    padding: 5px;
    background: var(--default-box-color);
    border: 1px solid var(--fa-card-border);
    border-radius: 4px;
  }

  &__button {
    width: 22px;
    height: 7px;
    border-radius: 3px;
  }

  &__chart {
    display: flex;
    flex: 1;
    gap: 3px;
    align-items: flex-end;
    justify-content: center;

    span {
      width: 7px;
      border-radius: 2px 2px 0 0;
    }
  }
}
</style>
