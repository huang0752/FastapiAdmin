<template>
  <div>
    <FaSectionTitle :title="$t('setting.themePreset.title')" class="mt-10" />
    <div class="setting-box-wrap">
      <div class="setting-item" @click="handleSelect('default')">
        <div class="box" :class="{ 'is-active': themePreset === 'default' }">
          <FaThemePresetPreview :tokens="defaultTokens" :primary="factoryPrimary" />
        </div>
        <p class="name">{{ $t("setting.themePreset.presets.default") }}</p>
      </div>
      <div
        v-for="preset in THEME_PRESETS"
        :key="preset.code"
        class="setting-item"
        @click="handleSelect(preset.code)"
      >
        <div class="box" :class="{ 'is-active': themePreset === preset.code && !isCustomized }">
          <FaThemePresetPreview :tokens="modeTokens(preset)" :primary="modePrimary(preset)" />
        </div>
        <p class="name">{{ $t(`setting.themePreset.presets.${preset.nameKey}`) }}</p>
      </div>
    </div>
    <p v-if="isCustomized" class="theme-preset-hint">
      {{ $t("setting.themePreset.custom") }} · {{ $t("setting.themePreset.hint") }}
    </p>
    <p v-else class="theme-preset-hint">{{ $t("setting.themePreset.hint") }}</p>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { storeToRefs } from "pinia";
import { SETTING_DEFAULT_CONFIG } from "@/config/setting";
import {
  THEME_PRESETS,
  type ThemePreset,
  type ThemePresetModeTokens,
  type ThemePresetCode,
} from "@/config/themePresets";
import { useThemePreset } from "@/hooks/core/useThemePreset";
import { useSettingsStore } from "@stores";

defineOptions({ name: "FaThemePresetSettings" });

const store = useSettingsStore();
const { themePreset, isDark, presetAppliedPrimary, presetAppliedMenuTheme } = storeToRefs(store);
const { selectPreset } = useThemePreset();
const factoryPrimary = SETTING_DEFAULT_CONFIG.systemThemeColor;
const defaultTokens: ThemePresetModeTokens = {
  sidebarBackground: "#FFFFFF",
  sidebarText: "#29343D",
  sidebarIcon: "#64748B",
  sidebarTitle: "#383853",
  sidebarActiveBackground: "#EDF2FF",
  sidebarActiveText: factoryPrimary,
  chartPalette: [
    factoryPrimary,
    "#4ABEFF",
    "#14DEBA",
    "#FFAF20",
    "#FA8A6C",
    "#B48DF3",
    "#60C041",
    "#909399",
  ],
};

const isCustomized = computed(
  () =>
    themePreset.value !== "default" &&
    (presetAppliedPrimary.value === null || presetAppliedMenuTheme.value === null)
);
const modeTokens = (preset: ThemePreset) => (isDark.value ? preset.modes.dark : preset.modes.light);
const modePrimary = (preset: ThemePreset) =>
  isDark.value ? preset.primary.dark : preset.primary.light;
const handleSelect = (code: ThemePresetCode) => {
  if (selectPreset(code)) store.reload();
};
</script>

<style scoped lang="scss">
.theme-preset-hint {
  margin: 4px 0 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--fa-gray-600);
  text-align: center;
}
</style>
