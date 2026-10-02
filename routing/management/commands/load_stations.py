import pandas as pd
from django.conf import settings
from django.core.management.base import BaseCommand

from routing.models import Station


class Command(BaseCommand):
    help = "Load data/stations_clean.csv into the Station table"

    def handle(self, *args, **options):
        df = pd.read_csv(settings.BASE_DIR / "data" / "stations_clean.csv")
        Station.objects.all().delete()
        Station.objects.bulk_create(
            [
                Station(
                    truckstop_id=int(r.truckstop_id),
                    name=r.name,
                    address=r.address,
                    city=r.city,
                    state=r.state,
                    price=float(r.price),
                    lat=float(r.lat),
                    lng=float(r.lng),
                )
                for r in df.itertuples()
            ],
            batch_size=1000,
        )
        self.stdout.write(self.style.SUCCESS(f"Loaded {Station.objects.count()} stations"))