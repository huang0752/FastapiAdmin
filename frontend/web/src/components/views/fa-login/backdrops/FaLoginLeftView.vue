<template>
  <div
    v-if="poster && productBrand"
    class="login-left-view brand-poster"
    :class="`brand-poster--${poster.siteTone}`"
    :style="posterThemeStyle"
  >
    <div class="brand-layer poster-enter poster-enter--brand">
      <FaLogo class="brand-layer__logo" size="42" :src="productBrand.logo" />
      <div>
        <strong>{{ productBrand.title }}</strong>
        <span>{{ poster.siteTone === "data360" ? "华夏电投" : "中能电投" }}</span>
      </div>
      <small>{{ displayVersion }}</small>
    </div>

    <div class="spatial-layer" aria-hidden="true">
      <span class="spatial-grid" /><span class="spatial-glow" /><span class="spatial-noise" />
    </div>

    <section class="narrative-layer poster-enter poster-enter--scene">
      <LoginBrandScene :scene="poster.scene" />
    </section>

    <div class="status-layer poster-enter poster-enter--status">
      <div v-for="(status, index) in poster.statuses" :key="status" class="status-chip">
        <i :style="{ '--status-index': index }" />
        <span>{{ status }}</span>
      </div>
    </div>

    <div class="copy-layer poster-enter poster-enter--copy">
      <div class="copy-layer__eyebrow">
        <span v-for="keyword in poster.keywords" :key="keyword">{{ keyword }}</span>
      </div>
      <h2>{{ poster.headline }}</h2>
      <p>{{ poster.description }}</p>
    </div>
  </div>

  <div v-else class="login-left-view login-left-view--legacy">
    <div v-if="!hideTopBranding" class="legacy-logo">
      <FaLogo size="46" :src="webLogoSrc" />
      <h1>{{ siteTitle }}</h1>
      <small>{{ displayVersion }}</small>
    </div>
    <div class="legacy-art">
      <img v-if="loginBgSrc" :src="loginBgSrc" alt="" /><FaThemeSvg
        v-else
        :src="loginIcon"
        size="100%"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import AppConfig from "@/config";
import loginIcon from "@fa_imgs/background.svg";
import { useConfigStore } from "@stores";
import { defaultAssemblySummary } from "@/config/assembly/default";
import { resolveSiteBrandTheme } from "@/config/brand/siteBrandTheme";
import { resolveLoginBrandPoster } from "@/config/brand/loginBrandPoster";
import LoginBrandScene from "@/components/brand/LoginBrandScene.vue";
import "@/styles/brand/login-brand-poster.scss";

defineOptions({ name: "FaLoginLeftView" });
interface Props {
  hideContent?: boolean;
  hideTopBranding?: boolean;
}
withDefaults(defineProps<Props>(), { hideContent: false, hideTopBranding: false });

const configStore = useConfigStore();
const siteIdentity = computed(
  () => configStore.siteConfigData.site_code?.config_value || window.location.hostname
);
const productBrand = computed(() =>
  resolveSiteBrandTheme(defaultAssemblySummary.name, siteIdentity.value)
);
const poster = computed(() =>
  productBrand.value
    ? resolveLoginBrandPoster(productBrand.value.site, productBrand.value.system)
    : null
);
const posterThemeStyle = computed(() =>
  productBrand.value
    ? {
        "--poster-primary": productBrand.value.tokens.primary,
        "--poster-accent": productBrand.value.tokens.highlight,
        "--poster-paper": productBrand.value.tokens.paper,
        "--poster-base": productBrand.value.tokens.base,
      }
    : undefined
);
const webLogoSrc = computed(
  () => productBrand.value?.logo || configStore.configData.tenant_logo?.config_value?.trim()
);
const siteTitle = computed(
  () =>
    productBrand.value?.title ||
    configStore.configData.tenant_name?.config_value?.trim() ||
    AppConfig.systemInfo.name
);
const displayVersion = computed(() => {
  const value = configStore.configData.tenant_version?.config_value?.trim() || "3.0.0";
  return /^[vV]/.test(value) ? value : `v${value}`;
});
const loginBgSrc = computed(
  () => configStore.configData.login_bg?.config_value?.trim() || undefined
);
</script>
