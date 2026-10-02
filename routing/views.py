from django.http import HttpResponse
from rest_framework.response import Response
from rest_framework.views import APIView

from .services import PlannerError, build_plan


class RoutePlanView(APIView):
    """GET /api/route/?start=Dallas, TX&finish=Seattle, WA
       POST /api/route/  {"start": "...", "finish": "..."}"""

    def get(self, request):
        return self._handle(request.query_params)

    def post(self, request):
        return self._handle(request.data)

    def _handle(self, params):
        start, finish = params.get("start"), params.get("finish")

        if not start or not finish:
            return Response(
                {"error": "Both 'start' and 'finish' are required."},
                status=400
            )

        try:
            plan = build_plan(str(start), str(finish))
        except PlannerError as exc:
            return Response(
                {"error": str(exc)},
                status=exc.status
            )

        return Response(plan)


MAP_PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Fuel route map</title>
<meta name="referrer" content="strict-origin-when-cross-origin">

<link
    rel="stylesheet"
    href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
>

<style>
html, body, #map {
    height: 100%;
    margin: 0;
}

#info {
    position: absolute;
    z-index: 1000;
    top: 10px;
    right: 10px;
    background: #fff;
    padding: 10px 14px;
    border-radius: 8px;
    font: 14px sans-serif;
    box-shadow: 0 1px 6px #0004;
}
</style>
</head>

<body>

<div id="info">Loading route...</div>
<div id="map"></div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>

<script>
const map = L.map('map');

L.tileLayer(
    'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    {
        attribution: '&copy; OpenStreetMap contributors'
    }
).addTo(map);

fetch('/api/route/' + location.search)
    .then(r => r.json())
    .then(d => {

        if (d.error) {
            document.getElementById('info').textContent = d.error;
            return;
        }

        const line = L.geoJSON(
            d.route,
            {
                style: {
                    color: '#2563eb',
                    weight: 4
                }
            }
        ).addTo(map);

        map.fitBounds(line.getBounds());

        L.marker([
            d.start.lat,
            d.start.lng
        ])
        .addTo(map)
        .bindPopup(
            'Start: ' + d.start.input
        );

        L.marker([
            d.finish.lat,
            d.finish.lng
        ])
        .addTo(map)
        .bindPopup(
            'Finish: ' + d.finish.input
        );

        d.fuel_stops.forEach(s => {

            L.circleMarker(
                [s.lat, s.lng],
                {
                    radius: 9,
                    color: '#dc2626',
                    fillOpacity: .9
                }
            )
            .addTo(map)
            .bindPopup(
                '<b>Stop ' +
                s.order +
                ': ' +
                s.station +
                '</b><br>' +
                s.city +
                ', ' +
                s.state +
                '<br>$' +
                s.price_per_gallon +
                '/gal<br>' +
                s.gallons_purchased +
                ' gal = $' +
                s.cost_usd
            );
        });

        document.getElementById('info').innerHTML =
            '<b>' +
            d.total_distance_miles +
            ' miles</b><br>' +
            d.fuel_stops.length +
            ' fuel stop(s)<br>' +
            'Total fuel: <b>$' +
            d.total_fuel_cost_usd +
            '</b>';
    });
</script>

</body>
</html>"""


def map_view(request):
    return HttpResponse(MAP_PAGE)