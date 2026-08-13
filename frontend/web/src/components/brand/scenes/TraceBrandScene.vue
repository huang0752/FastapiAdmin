<template>
  <svg class="brand-scene trace-batch-chain" viewBox="0 0 800 560" aria-hidden="true">
    <defs>
      <linearGradient id="trace-flow" x1="0" y1="0" x2="1" y2="1">
        <stop stop-color="var(--poster-primary)" />
        <stop offset="1" stop-color="var(--poster-accent)" />
      </linearGradient>
    </defs>
    <g class="scene-grid">
      <path d="M48 116H752M48 280H752M48 444H752M160 54V506M400 54V506M640 54V506" />
    </g>
    <path
      class="trace-route business-path"
      d="M92 390C184 390 184 174 292 174s104 214 216 214 104-190 204-190"
    />
    <g class="trace-ledger">
      <rect x="270" y="118" width="260" height="326" rx="28" />
      <path d="M316 194h168M316 238h112M316 326h168M316 370h96" />
    </g>
    <g class="scene-node node-a">
      <circle cx="92" cy="390" r="22" />
      <path d="m82 390 7 7 14-17" />
    </g>
    <g class="scene-node node-b">
      <circle cx="292" cy="174" r="18" />
      <path d="m284 174 6 6 12-15" />
    </g>
    <g class="scene-node node-c">
      <circle cx="508" cy="388" r="18" />
      <path d="m500 388 6 6 12-15" />
    </g>
    <g class="scene-node node-d">
      <circle cx="712" cy="198" r="22" />
      <path d="m702 198 7 7 14-17" />
    </g>
  </svg>
</template>
