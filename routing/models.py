from django.db import models


class Station(models.Model):
    truckstop_id = models.IntegerField(unique=True)
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=200)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2)
    price = models.FloatField(help_text="USD per gallon")
    lat = models.FloatField()
    lng = models.FloatField()

    class Meta:
        indexes = [models.Index(fields=["city", "state"])]

    def __str__(self):
        return f"{self.name} - {self.city}, {self.state} (${self.price:.3f})"