<template>
  <main class="verify-page">
    <section v-if="loading" class="state">正在核验软件使用证明…</section>
    <section v-else-if="!certificate" class="state danger"><h1>无法核验此证明</h1><p>二维码或查验地址无效，请重新获取。</p></section>
    <article v-else class="document">
      <header><span>FASTAPIADMIN</span><strong>SOFTWARE USAGE CERTIFICATE</strong></header>
      <p class="system">{{ certificate.system_name }} · {{ certificate.system_version }}</p>
      <h1>企业软件使用证明</h1>
      <div class="status" :class="{ invalid: !certificate.currently_valid }">{{ certificate.status_label }}</div>
      <dl><div><dt>证明编号</dt><dd>{{ certificate.certificate_no }}</dd></div><div><dt>企业名称</dt><dd>{{ certificate.enterprise_name }}</dd></div><div><dt>统一社会信用代码</dt><dd>{{ certificate.social_credit_code }}</dd></div><div><dt>授权期限</dt><dd>{{ certificate.start_time }} — {{ certificate.end_time }}</dd></div></dl>
      <footer>证明内容以系统当前租户及授权信息为准。</footer>
    </article>
  </main>
</template>

<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import UsageCertificateAPI, { type UsageCertificatePublic } from "@/api/module_platform/usage_certificate";

defineOptions({ name: "PublicUsageCertificateVerify" });
const route = useRoute();
const loading = ref(true);
const certificate = ref<UsageCertificatePublic | null>(null);
onMounted(async () => {
  try {
    const { data: res } = await UsageCertificateAPI.publicVerify(String(route.params.token || ""));
    certificate.value = res?.data || null;
  } catch { certificate.value = null; }
  finally { loading.value = false; }
});
</script>

<style scoped>
.verify-page { min-height: 100vh; padding: clamp(18px, 5vw, 60px); background: #eef2f5; color: #18222b; }.document,.state { width: min(820px,100%); margin: 0 auto; background: #fff; border-top: 8px solid #245f8f; box-shadow: 0 22px 60px rgb(24 34 43 / 10%); padding: clamp(24px,6vw,64px); }.document header { display:flex;justify-content:space-between;border-bottom:1px solid #cbd5dd;padding-bottom:16px;color:#607080;font-size:12px;letter-spacing:.12em }.system { margin-top:64px;color:#245f8f }.document h1 { font-size:clamp(30px,6vw,46px);margin:8px 0 28px }.status { display:inline-block;padding:9px 16px;background:#23745f;color:#fff;font-weight:700 }.status.invalid { background:#a23a32 }.document dl { display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-top:40px }.document dt { color:#6e7c86;font-size:13px }.document dd { margin:7px 0 0;font-weight:650;overflow-wrap:anywhere }.document footer { margin-top:56px;padding-top:18px;border-top:1px solid #d7dfe5;color:#667680;font-size:12px }.state { text-align:center }.danger { border-top-color:#a23a32 } @media(max-width:600px){.document dl{grid-template-columns:1fr}.document header{flex-direction:column;gap:8px}.system{margin-top:38px}}
</style>
