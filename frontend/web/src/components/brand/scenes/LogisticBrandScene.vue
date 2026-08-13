<template>
  <svg class="brand-scene logistic-temperature-lane" viewBox="0 0 800 560" aria-hidden="true">
    <g class="cold-lanes">
      <path d="M50 132h700M50 280h700M50 428h700" />
      <path d="M144 58v444M400 58v444M656 58v444" />
    </g>
    <path
      class="logistic-route business-path"
      d="M70 412c118-6 136-164 258-160s144 148 248 92 66-196 162-200"
    />
    <g class="cold-truck">
      <path d="M242 240h228l82 72h58v92H242Z" />
      <path d="M474 274h58l48 42H474Z" />
      <circle cx="324" cy="410" r="38" />
      <circle cx="532" cy="410" r="38" />
    </g>
    <g class="temperature-core">
      <circle cx="418" cy="168" r="62" />
      <path d="M418 126v84m-36-63 72 42m-72 0 72-42" />
    </g>
    <circle class="scene-node node-a" cx="70" cy="412" r="18" />
    <circle class="scene-node node-b" cx="738" cy="144" r="18" />
  </svg>
</template>
