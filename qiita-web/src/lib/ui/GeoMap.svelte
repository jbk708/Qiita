<script lang="ts">
  // Real basemap via Leaflet + OpenStreetMap tiles. Client-only (SPA): Leaflet
  // is dynamically imported in onMount. Points are circleMarkers (vector), one
  // bubble per (station, sample-type), sized by count and colored by
  // qiita_sample_type — the one controlled vocab that's reliable across studies.
  import 'leaflet/dist/leaflet.css';
  import { onMount, onDestroy } from 'svelte';
  import type { Map as LMap, LayerGroup } from 'leaflet';

  type Pt = { lat: number; lon: number; label?: string; type?: string };
  let { points }: { points: Pt[] } = $props();

  // Palette assigned per distinct sample-type value (generalizes to any study's
  // vocabulary); 'unspecified' is always gray.
  const PALETTE = [
    '#0d9488', '#f59e0b', '#0ea5e9', '#8b5cf6', '#e11d48',
    '#10b981', '#f97316', '#6366f1', '#db2777', '#65a30d'
  ];
  const UNSPEC = '#9ca3af';

  let el: HTMLDivElement;
  let map = $state<LMap | null>(null);
  let layer: LayerGroup | null = null;
  let leaflet: typeof import('leaflet') | null = null;

  const valid = $derived(
    points.filter(
      (p) => Number.isFinite(p.lat) && Number.isFinite(p.lon) && Math.abs(p.lat) <= 90 && Math.abs(p.lon) <= 180
    )
  );
  const realTypes = $derived(
    [...new Set(valid.map((p) => p.type ?? 'unspecified').filter((t) => t !== 'unspecified'))].sort()
  );
  function colorFor(t: string): string {
    if (t === 'unspecified') return UNSPEC;
    const i = realTypes.indexOf(t);
    return i < 0 ? UNSPEC : PALETTE[i % PALETTE.length];
  }
  const byType = $derived.by(() => {
    const c = new Map<string, number>();
    for (const p of valid) {
      const t = p.type ?? 'unspecified';
      c.set(t, (c.get(t) ?? 0) + 1);
    }
    return [...c.entries()].sort((a, b) => b[1] - a[1]);
  });
  const stations = $derived.by(() => {
    const m = new Map<string, { lat: number; lon: number; count: number; type: string }>();
    for (const p of valid) {
      const t = p.type ?? 'unspecified';
      const k = `${p.lat},${p.lon},${t}`;
      const g = m.get(k) ?? { lat: p.lat, lon: p.lon, count: 0, type: t };
      g.count++;
      m.set(k, g);
    }
    return [...m.values()];
  });

  onMount(async () => {
    leaflet = await import('leaflet');
    map = leaflet.map(el, { worldCopyJump: true }).setView([20, 0], 1);
    leaflet
      .tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; OpenStreetMap contributors',
        maxZoom: 19
      })
      .addTo(map);
    layer = leaflet.layerGroup().addTo(map);
  });

  onDestroy(() => map?.remove());

  $effect(() => {
    if (!map || !layer || !leaflet) return;
    layer.clearLayers();
    for (const s of stations) {
      const c = colorFor(s.type);
      leaflet
        .circleMarker([s.lat, s.lon], {
          radius: 4 + Math.sqrt(s.count) * 1.8,
          color: c,
          weight: 1,
          fillColor: c,
          fillOpacity: 0.55
        })
        .bindTooltip(`${s.count} × ${s.type} · ${s.lat.toFixed(3)}, ${s.lon.toFixed(3)}`)
        .addTo(layer);
    }
    if (stations.length) {
      const b = leaflet.latLngBounds(stations.map((s) => [s.lat, s.lon] as [number, number]));
      map.fitBounds(b.pad(0.3), { maxZoom: 8 });
    }
  });
</script>

<div bind:this={el} class="h-96 w-full rounded-md ring-1 ring-gray-200"></div>
<div class="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-gray-500">
  <span>{valid.length} geolocated · {stations.length} stations · colored by qiita_sample_type</span>
  <span class="flex flex-wrap items-center gap-x-3 gap-y-1">
    {#each byType as [t, n]}
      <span class="flex items-center gap-1.5">
        <span class="inline-block size-2.5 rounded-full" style="background:{colorFor(t)}"></span>{t} ({n})
      </span>
    {/each}
  </span>
</div>
