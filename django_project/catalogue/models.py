"""Product catalogue for Indian medicines.

The core idea: a Product (brand SKU) never stores substitutes as a list.
Substitution is derived at query time from a normalised composition_key,
so adding one new product automatically makes it a substitute for every
existing product that shares its salts, strengths and dosage form.
"""


from django.db import models


def slugify(text: str) -> str:
    import re
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug or "unknown"


class Manufacturer(models.Model):
    """A medicine manufacturer."""

    name = models.CharField(max_length=200, unique=True)
    slug = models.SlugField(max_length=200, unique=True, index=True)

    class Meta:
        app_label = "catalogue"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Molecule(models.Model):
    """A canonical active ingredient.

    Brand labels spell these many ways; `MoleculeAlias` maps every observed
    spelling back to one row here.
    """

    canoncial_name = models.CharField(max_length=160, unique=True)
    slug = models.SlugField(max_length=160, unique=True, index=True)
    atc_code = models.CharField(max_length=16, blank=True, null=True, index=True)
    is_banned = models.BooleanField(default=False)
    banned_reference = models.CharField(max_length=255, blank=True, null=True)

    aliases = models.ManyToManyField(
        "self", through="MoleculeAlias", blank=True
    )

    def __str__(self):
        return self.canonical_name


class MoleculeAlias(models.Model):
    """Alternate spellings: Amoxycillin / Amoxicillin / Amoxycillin Trihydrate."""

    molecule = models.ForeignKey(Molecule, on_delete=models.CASCADE)
    alias = models.CharField(max_length=160, db_index=True)
    normalised = models.CharField(max_length=160, db_index=True)

    class Meta:
        app_label = "catalogue"
        unique_together = ("molecule", "normalised")

    def __str__(self):
        return f"{self.molecule.canonical_name} ~> {self.alias}"


class Product(models.Model):
    """A sellable pack. One row per brand + strength + pack size."""

    sku = models.CharField(max_length=64, unique=True, db_index=True)
    brand_name = models.CharField(max_length=240)
    composition_text = models.CharField(max_length=500)  # as printed on the pack
    composition_key = models.CharField(max_length=500, db_index=True)  # derived
    molecule_key = models.CharField(max_length=400, db_index=True)  # salts only, no strength

    dosage_form = models.CharField(max_length=60)  # TABLET / CAPSULE / SYRUP
    form_family = models.CharField(max_length=40)  # ORAL_SOLID / ORAL_LIQUID / ...

    pack_label = models.CharField(max_length=120)  # "Strip of 15 tablets"
    pack_units = models.IntegerField(blank=True, null=True)

    manufacturer = models.ForeignKey(
        Manufacturer, on_delete=models.SET_NULL, null=True
    )

    # Pricing (in paise, never float)
    mrp_paise = models.IntegerField()
    selling_price_paise = models.IntegerField()

    # Regulatory
    drug_schedule = models.CharField(max_length=8, default="OTC")
    is_prescription_required = models.BooleanField(default=False)
    is_nlem = models.BooleanField(default=False)
    hsn_code = models.CharField(max_length=12, blank=True, null=True)

    # Merchandising
    category = models.CharField(max_length=80, blank=True, null=True, db_index=True)
    is_discontinued = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    rating = models.DecimalField(max_length=3, decimal_places=1, blank=True, null=True)

    # Editorial content
    content_source = models.CharField(max_length=120)
    content_reviewed_by = models.CharField(max_length=160, blank=True, null=True)
    content_reviewed_at = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "catalogue"
        ordering = ["brand_name"]
        indexes = [
            models.Index(fields=["composition_key", "is_active"]),
            models.Index(fields=["molecule_key", "is_active"]),
        ]

    def __str__(self):
        return self.brand_name

    @property
    def discount_percent(self):
        if not self.mrp_paise:
            return 0
        return round((self.mrp_paise - self.selling_price_paise) * 100 / self.mrp_paise)

    def price_breaches_ceiling(self):
        """DPCO ceiling prices are a hard legal cap, not a guideline."""
        from django.db.models import F
        if self.nppa_ceiling_price_paise is None:
            return False
        return self.selling_price_paise > self.nppa_ceiling_price_paise


class ProductIngredient(models.Model):
    """One salt + strength within a product. A fixed-dose combination has
    several of these rows."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="ingredients")
    molecule = models.ForeignKey(Molecule, on_delete=models.CASCADE)

    strength_value = models.DecimalField(max_length=12, decimal_places=4)
    strength_unit = models.CharField(max_length=16)  # as printed
    strength_base_value = models.DecimalField(max_length=16, decimal_places=6)  # normalised
    strength_base_unit = models.CharField(max_length=16)  # MG / ML / IU
    position = models.IntegerField(default=0, nullable=False)

    class Meta:
        app_label = "catalogue"
        unique_together = ("product", "position")

    def __str__(self):
        return f"{self.molecule.canonical_name} {self.strength_value}{self.strength_unit}"