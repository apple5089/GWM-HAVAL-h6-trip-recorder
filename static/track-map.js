/* Raw positions are displayed without coordinate conversion or road snapping. */
(function (root) {
  'use strict';
  function valid(p) {
    return p && Number.isFinite(p.lat) && Number.isFinite(p.lon) &&
      Math.abs(p.lat) <= 90 && Math.abs(p.lon) <= 180 && (p.lat !== 0 || p.lon !== 0);
  }
  function segments(points) {
    const groups = []; let group = [];
    for (const p of points) {
      if (!valid(p) || p.break_before) {
        if (group.length) groups.push(group);
        group = [];
      }
      if (valid(p)) group.push([p.lat, p.lon]);
    }
    if (group.length) groups.push(group);
    return groups;
  }
  if (typeof module !== 'undefined') module.exports = {valid, segments};
  if (!root.document) return;
  let map, tiles, lines, lastPosition, routeBounds, positioned = false, tileErrors = 0;
  function init() {
    if (map) return;
    if (!root.L) throw Error('地图组件加载失败，请刷新页面。');
    map = L.map('plot', {scrollWheelZoom: false}).setView([35, 105], 4);
    document.getElementById('plot').classList.add('night-map');
    document.getElementById('nightMap').addEventListener('change',event=>document.getElementById('plot').classList.toggle('night-map',event.target.checked));
    tiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors'
    }).addTo(map);
    tiles.on('tileerror', () => {
      tileErrors++;
      document.getElementById('mapNetwork').textContent = '部分道路瓦片未能加载，请检查 NAS 访问设备的网络。轨迹仍可显示。';
    });
    tiles.on('loading', () => {tileErrors = 0;});
    tiles.on('load', () => {
      if (!tileErrors) document.getElementById('mapNetwork').textContent = '';
    });
    lines = L.featureGroup().addTo(map);
    document.getElementById('basemap').addEventListener('change', event => {
      if (event.target.checked) tiles.addTo(map); else map.removeLayer(tiles);
    });
  }
  function fit() {
    if (routeBounds && routeBounds.isValid()) map.fitBounds(routeBounds, {padding: [28, 28], maxZoom: 16});
    else locate();
  }
  function locate() {
    if (lastPosition) map.setView(lastPosition.getLatLng(), 16);
  }
  function render(points, latest, resetView) {
    init(); lines.clearLayers();
    const clean = points.filter(valid);
    for (const group of segments(points)) {L.polyline(group,{color:'#35e5ff',weight:15,opacity:.18,className:'routeGlow'}).addTo(lines);L.polyline(group,{color:'#43e8ff',weight:5,opacity:.95}).addTo(lines);L.polyline(group,{color:'#d9fbff',weight:1,opacity:.8}).addTo(lines);}
    if (clean.length) {
      for (const [p, color, label] of [[clean[0], '#65f4bc', '起点'], [clean[clean.length - 1], '#ff80bf', '终点']]) {
        L.circleMarker([p.lat, p.lon], {radius: 6, color, fillColor: color, fillOpacity: 1})
          .bindTooltip(label).addTo(lines);
      }
    }
    routeBounds = lines.getBounds();
    if (lastPosition) {map.removeLayer(lastPosition); lastPosition = null;}
    if (latest && latest.has_location && valid({lat: latest.latitude, lon: latest.longitude})) {
      const label = document.createElement('span');
      label.textContent = '最近云端位置 · ' + new Date(latest.source_ms).toLocaleString() +
        (latest.age_seconds > 300 ? '（陈旧定位，不计入新轨迹）' : '');
      lastPosition = L.circleMarker([latest.latitude, latest.longitude],
        {radius: 9, color: '#4568b2', fillColor: '#4568b2', fillOpacity: .55}).bindTooltip(label).addTo(map);
    }
    document.getElementById('locateVehicle').disabled = !lastPosition;
    document.getElementById('fitRoute').disabled = !clean.length && !lastPosition;
    if (resetView || !positioned) {
      fit(); positioned = !!(clean.length || lastPosition);
    }
    document.getElementById('plotHint').textContent = clean.length ?
      `${clean.length} 个原始坐标点 · 绿色起点 / 粉色终点 · 蓝色为最近云端位置。断档不连线。` :
      (lastPosition ? '暂无新鲜轨迹点。蓝色标记仅为最近云端位置，请留意采样时间。' : '暂无可显示的车辆位置或轨迹。');
  }
  root.H6TrackMap = {render, fit, locate};
})(typeof window !== 'undefined' ? window : globalThis);
