# -*- coding: utf-8 -*-

from openerp import models, fields, api, _
from openerp.tools.float_utils import float_round
from openerp.exceptions import ValidationError
import addons.decimal_precision as dp
import sys

import logging

_logger = logging.getLogger(__name__)


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    ingredient_list = fields.Html(string='Ingredients List', related='product_variant_ids.ingredient_list')
    calculated_norm_weight = fields.Float(string='Berechnetes Gewicht pro ME (g)', related="product_variant_ids.calculated_norm_weight", store=True)
    norm_weight_diff = fields.Float(string='Abweichung (g)', related="product_variant_ids.norm_weight_diff", store=True)
    deviation = fields.Float(string='Abweichung (Prozent)', related="product_variant_ids.deviation", store=True)
    allow_standard_price_zero = fields.Boolean(string='Price is zero')

    def _recursive_bom_ingredients_complete(self, qty=0, uom=0, level=0, ingredients=None):
        ingredients = ingredients or {}
        level += 1

        if not self.nutrition:
            raise ValidationError(_("Product %s is not activated for nutrition!") % self.display_name)

        if self.bom_ids:
            bom = self.bom_ids[0]
            bom_uom = bom.product_uom
            bom_qty = bom.product_qty
            if uom and uom != bom.product_uom:
                bom_qty = uom._compute_qty(bom_uom.id, bom.product_qty, uom.id, round=False)
            multiplier = qty / bom_qty

            if len(self.bom_ids.ids) > 1:
                _logger.debug('\n--------- #%s Multiple BoMs (%s) ---------', level, self.display_name)
                _logger.debug('\n--------- Taking first BoM to calculate ---------', level, self.display_name)
                _logger.debug('\n Qty: %s %s | BoM Result Qty: %s %s | Multiplier: %s', qty, uom.name, bom_qty, bom_uom.name, multiplier)
                # Ask which one should be taken, for now we take the first bom
                for bom in [self.bom_ids[0]]:
                    for bom_line in bom.bom_line_ids:
                        partial_qty = bom_line.product_qty * multiplier
                        ingredients = bom_line.product_id.product_tmpl_id._recursive_bom_ingredients_complete(qty=partial_qty, uom=bom_line.product_uom, level=level, ingredients=ingredients)
            else:
                _logger.debug('\n--------- #%s Single BoM (%s) ---------', level, self.display_name)
                _logger.debug('\n Qty: %s %s | BoM Result Qty: %s %s | Multiplier: %s', qty, uom.name, bom_qty, bom_uom.name, multiplier)
                for bom_line in bom.bom_line_ids:
                    partial_qty = bom_line.product_qty * multiplier
                    ingredients = bom_line.product_id.product_tmpl_id._recursive_bom_ingredients_complete(qty=partial_qty, uom=bom_line.product_uom, level=level, ingredients=ingredients)
        else:
            _logger.debug('--------- #%s Product (%s) to List ---------', level, self.display_name)
            ingredients = self.add_to_ingridients_list_complete(ingredients, qty=qty, uom=uom)

        return ingredients

    @api.multi
    def add_to_ingridients_list_complete(self, ingredients, qty=0, uom=False):

        if self.norm_weight <= 0:
            raise ValidationError(_("Norm weight for product %s must be greater than 0!") % self.display_name)

        if uom and uom != self.uom_id:
            qty = uom._compute_qty(uom.id, qty, self.uom_id.id, round=False)

        multiplier = self.norm_weight * qty / 100
        _logger.debug("Facts: %s %s (%s kcal) of %s", qty, self.uom_id.name, self.energy_calories * multiplier, self.display_name)

        if self in ingredients:
            ingredients[self]['norm_weight'] += self.norm_weight * qty

            ingredients[self].update({
                'ingredient_name': (self.ingredient_name or self.name).strip(),
                'yeast_free': self.yeast_free,
                'allergen_ids': self.allergen_ids,
            })
        else:
            ingredients[self] = {
                'norm_weight': self.norm_weight * qty,
                'ingredient_name': (self.ingredient_name or self.name).strip(),
                'yeast_free': self.yeast_free,
                'allergen_ids': self.allergen_ids,
            }

        return ingredients

    @api.multi
    def write_nutrition_facts_complete(self, ingredients):
        tuple_list = []
        yeast_free = True
        allergen_ids = self.env['product.food.allergen']
        total_norm_weight = 0
        for info in ingredients.itervalues():
            total_norm_weight += info['norm_weight']
            tuple_list.append((info['ingredient_name'], info['norm_weight'], info['yeast_free'], info['allergen_ids']))
            allergen_ids |= info['allergen_ids']
            if not info['yeast_free']:
                yeast_free = False

        ingredient_list = sorted(tuple_list, key=lambda info: info[1], reverse=True)

        norm_weight_diff = total_norm_weight - self.norm_weight
        deviation = (total_norm_weight / self.norm_weight - 1) * 100
        show_percentage = True
        if abs(deviation) > 20:
            show_percentage = False

        ingredient_names = []
        for info in ingredient_list:
            ingredient_name = info[0]
            if info[3]:
                if len(ingredient_name.split('*')) > 1:
                    ingredient_name = ''
                    for part in info[0].split('*'):
                        if ',' in part or ' ' in part:
                            ingredient_name += part
                        else:
                            ingredient_name += '<strong>' + part + '</strong>'
                else:
                    if show_percentage:
                        ingredient_name = '<strong>%s (%s%%)</strong>' % (ingredient_name, int(round(info[1] / total_norm_weight * 100)))
                    else:
                        ingredient_name = '<strong>%s</strong>' % ingredient_name

            _logger.debug("%s g of %s", round(info[1], 2), ingredient_name)

            ingredient_names.append(ingredient_name)

        _logger.debug("Abweichung: %s%%", round(deviation, 2))
        _logger.debug('Total Norm Weight: %s g', round(total_norm_weight, 2))

        self.write({
            'ingredient_list': ', '.join(map(unicode, ingredient_names)) or self.ingredient_name or self.name,
            'yeast_free': yeast_free,
            'calculated_norm_weight': total_norm_weight,
            'deviation': deviation,
            'norm_weight_diff': norm_weight_diff,
            'allergen_ids': [(6, 0, allergen_ids.ids)],
        })

    @api.multi
    def compute_labeling_facts(self):
        qty = 1
        uom = self.uom_id
        ingredients = self._recursive_bom_ingredients_complete(qty=qty, uom=uom)
        self.write_nutrition_facts_complete(ingredients)

    @api.multi
    def batch_compute_labeling(self):
        for template in self:
            try:
                template.compute_labeling_facts()
            except ValidationError as e:
                _logger.error('ValidationError: %s', e[1])
            except:
                e = sys.exc_info()
                _logger.error('%s: %s', e[0], e[1])

    def _calc_price(self, cr, uid, bom, test=False, real_time_accounting=False, context=None):
        context = context or {}
        price = super(ProductTemplate, self)._calc_price(cr, uid, bom, test=test, real_time_accounting=real_time_accounting, context=context)
        if price > 0 or test:
            return price

        uom_obj = self.pool.get("product.uom")
        message = ''
        fail = False
        for sbom in bom.bom_line_ids:
            my_qty = sbom.product_qty / sbom.product_efficiency
            if not sbom.attribute_value_ids:
                price_product = uom_obj._compute_price(cr, uid, sbom.product_id.uom_id.id, sbom.product_id.standard_price, sbom.product_uom.id) * my_qty
                if price_product <= 0:
                    if not (sbom.product_id.allow_standard_price_zero and price_product == 0):
                        fail = True
                        product_name = bom.product_id and bom.product_id.name or bom.product_tmpl_id and bom.product_tmpl_id.name
                        message += message + u'%s hat einen Preis von EUR %s definiert, weshalb kein gültiger Preis für das Produkt %s ermittelt werden konnte!\n' % (sbom.product_id.name, price_product, product_name)
        if fail:
            raise ValidationError(message)
        return price

    @api.multi
    def batch_compute_price(self):
        for template in self:
            template.compute_price([], template_ids=[template.id], recursive=True, real_time_accounting=False, test=False)

    @api.multi
    def batch_compute_all(self):
        self.batch_compute_price()
        super(ProductTemplate, self).batch_compute_all()
        for template in self:
            try:
                template.compute_labeling_facts()
            except ValidationError as e:
                _logger.error('ValidationError: %s', e[1])
            except:
                e = sys.exc_info()
                _logger.error('%s: %s', e[0], e[1])


class ProductProduct(models.Model):
    _inherit = 'product.product'

    ingredient_list = fields.Html(string='Ingredients List')
    calculated_norm_weight = fields.Float(string='Berechnetes Gewicht pro ME (g)', digits_compute=dp.get_precision('Stock Weight'), default=0)
    norm_weight_diff = fields.Float(string='Abweichung (g)', default=0)
    deviation = fields.Float(string='Abweichung (Prozent)', default=0)

    @api.multi
    def compute_labeling_facts(self):
        if self.product_tmpl_id.product_variant_count <= 1:
            self.product_tmpl_id.compute_labeling_facts()

    @api.multi
    def batch_compute_labeling(self):
        for product in self:
            try:
                product.compute_labeling_facts()
            except ValidationError as e:
                _logger.error('ValidationError: %s', e[1])
            except:
                e = sys.exc_info()
                _logger.error('%s: %s', e[0], e[1])

    @api.multi
    def batch_compute_price(self):
        if self.product_tmpl_id.product_variant_count <= 1:
            self.product_tmpl_id.batch_compute_price()

    @api.multi
    def batch_compute_all(self):
        if self.product_tmpl_id.product_variant_count <= 1:
            self.product_tmpl_id.batch_compute_all()
