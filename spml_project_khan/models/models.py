from odoo import models, fields, api,_
from odoo.exceptions import AccessError, UserError
from datetime import datetime

class BankAccount(models.Model):
    _inherit = 'account.bank.statement.line'

    bank_account = fields.Many2one('account.account', 'Account')
    employee_name = fields.Many2one('hr.employee', 'Employee')


class Manufactoring(models.Model):
    _inherit = 'mrp.production'

    extra_costs = fields.One2many('extra.cost', 'raw_material_extra')

class ExtraCosts(models.Model):
    _name = 'extra.cost'

    raw_material_extra = fields.Many2one('mrp.production', string='Name')
    name_extra = fields.Char(string='Name')
    cost_extra = fields.Float(string='Extra Cost')

class MrpCostStructure(models.AbstractModel):
    _inherit = 'report.mrp_account_enterprise.mrp_cost_structure'
    _description = 'MRP Cost Structure Report'

    def get_lines(self, productions):
        ProductProduct = self.env['product.product']
        StockMove = self.env['stock.move']


        res = []
        docs = []
        extra_cost = 0.0

        cost=[]

        print("production",productions)
        for product in productions.mapped('product_id'):
            mos = productions.filtered(lambda m: m.product_id == product)
            print("product",product)
            total_cost = 0.0
            mrpproduct = self.env['mrp.production'].search([('id', '=', mos.id)])
            # print(mrpproduct.name)
            total_cost = 0.0
            for i in mrpproduct.extra_costs:
                extra_cost = extra_cost + i.cost_extra
                docs.append({
                    'name_extra': i.name_extra,
                    'cost_extra': i.cost_extra,
                })


            #get the cost of operations
            operations = []
            Workorders = self.env['mrp.workorder'].search([('production_id', 'in', mos.ids)])
            print("Workorders",Workorders)
            if Workorders:
                query_str = """SELECT w.operation_id, op.name, partner.name, sum(t.duration), wc.costs_hour
                                FROM mrp_workcenter_productivity t
                                LEFT JOIN mrp_workorder w ON (w.id = t.workorder_id)
                                LEFT JOIN mrp_workcenter wc ON (wc.id = t.workcenter_id )
                                LEFT JOIN res_users u ON (t.user_id = u.id)
                                LEFT JOIN res_partner partner ON (u.partner_id = partner.id)
                                LEFT JOIN mrp_routing_workcenter op ON (w.operation_id = op.id)
                                WHERE t.workorder_id IS NOT NULL AND t.workorder_id IN %s
                                GROUP BY w.operation_id, op.name, partner.name, t.user_id, wc.costs_hour
                                ORDER BY op.name, partner.name
                            """
                self.env.cr.execute(query_str, (tuple(Workorders.ids), ))
                for op_id, op_name, user, duration, cost_hour in self.env.cr.fetchall():
                    operations.append([user, op_id, op_name, duration / 60.0, cost_hour])

            #get the cost of raw material effectively used
            raw_material_moves = []
            query_str = """SELECT sm.product_id, sm.bom_line_id, abs(SUM(svl.quantity)), abs(SUM(svl.value))
                             FROM stock_move AS sm
                       INNER JOIN stock_valuation_layer AS svl ON svl.stock_move_id = sm.id
                            WHERE sm.raw_material_production_id in %s AND sm.state != 'cancel' AND sm.product_qty != 0 AND scrapped != 't'
                         GROUP BY sm.bom_line_id, sm.product_id"""
            self.env.cr.execute(query_str, (tuple(mos.ids), ))
            for product_id, bom_line_id, qty, cost in self.env.cr.fetchall():
                raw_material_moves.append({
                    'qty': qty,
                    'cost': cost,
                    'product_id': ProductProduct.browse(product_id),
                    'bom_line_id': bom_line_id
                })
                total_cost += cost

            #get the cost of scrapped materials
            scraps = StockMove.search([('production_id', 'in', mos.ids), ('scrapped', '=', True), ('state', '=', 'done')])
            uom = mos and mos[0].product_uom_id
            mo_qty = 0
            if not all(m.product_uom_id.id == uom.id for m in mos):
                uom = product.uom_id
                for m in mos:
                    qty = sum(m.move_finished_ids.filtered(lambda mo: mo.state != 'cancel' and mo.product_id == product).mapped('product_qty'))
                    if m.product_uom_id.id == uom.id:
                        mo_qty += qty
                    else:
                        mo_qty += m.product_uom_id._compute_quantity(qty, uom)
            else:
                for m in mos:
                    mo_qty += sum(m.move_finished_ids.filtered(lambda mo: mo.state != 'cancel' and mo.product_id == product).mapped('product_qty'))


            for m in mos:
                byproduct_moves = m.move_finished_ids.filtered(lambda mo: mo.state != 'cancel' and mo.product_id != product)

            res.append({
                'product': product,
                'mo_qty': mo_qty,
                'mo_uom': uom,
                'operations': operations,
                'currency': self.env.company.currency_id,
                'raw_material_moves': raw_material_moves,
                'total_cost': total_cost,
                'scraps': scraps,
                # 'name_extra': i.name_extra,
                'docs': docs,
                'mocount': len(mos),
                'byproduct_moves': byproduct_moves,
                'extra_cost': extra_cost

            })

            print(res)
        return res

    @api.model
    def _get_report_values(self, docids, data=None):
        productions = self.env['mrp.production']\
            .browse(docids)\
            .filtered(lambda p: p.state != 'cancel')
        res = None
        if all([production.state == 'done' for production in productions]):
            res = self.get_lines(productions)
        return {'lines': res}


class ProductTemplateCostStructure(models.AbstractModel):
    _inherit = 'report.mrp_account_enterprise.product_template_cost_structure'
    _description = 'Product Template Cost Structure Report'

    @api.model
    def _get_report_values(self, docids, data=None):
        productions = self.env['mrp.production'].search([('product_id', 'in', docids), ('state', '=', 'done')])
        res = self.env['report.mrp_account_enterprise.mrp_cost_structure'].get_lines(productions)
        return {'lines': res}

class MRPMaterial(models.Model):

    _inherit = 'mrp.production'
    _description = 'MRP Raw Material'

    state = fields.Selection(selection_add=[('raw', 'Request Raw Material'),('qc', 'QC Sample'),('transfer', 'QC Check')])

    def action_request(self):
        dropship_vals = []
        print("HEllo")
        self.write({'state': 'raw'})
        for company in self:
            location_id = self.env['stock.location'].search([('usage', '=', 'production')])
            print("RRW",location_id.name)
            sequence = self.env['ir.sequence'].search([
                ('code', '=', 'stock.dropshipping')])
            dropship_picking_type = self.env['stock.picking.type'].search([
                ('name', '=', 'Request Raw Material')], limit=1)

            product_car = self.env['product.product'].search([
                ('id', '=', company.product_id.id)], limit=1)

            dropship_vals.append({
                'name': 'Request Raw Material',
                'warehouse_id':self.env.user.company_id.id,
                'sequence_id': sequence.id,
                'code': 'internal',
                'default_location_src_id': location_id.id,
                'default_location_dest_id': self.location_dest_id.id,
                'sequence_code': 'RRM',

            })
            if not dropship_picking_type:
                print("Inside")


                self.env['stock.picking.type'].create(dropship_vals)
                dropship_picking_type = self.env['stock.picking.type'].search([
                    ('name', '=', 'Request Raw Material')], limit=1)
                picking = self.env['stock.picking'].create({
                    'location_id': location_id.id,
                    'location_dest_id': self.location_dest_id.id,
                    'partner_id': self._uid,
                    'origin': self.origin,
                    'picking_type_id': dropship_picking_type.id,
                    'immediate_transfer': True,
                })
                # print(picking)
                for i in company.move_raw_ids:
                    # print(i.product_id.name)

                    move_receipt_1 = self.env['stock.move'].create({
                        'name': i.name,
                        'product_id': i.product_id.id,
                        'product_uom_qty': i.product_qty,
                        'quantity_done': company.product_qty,
                        'product_uom': i.product_id.uom_id.id,
                        'picking_id': picking.id,
                        'picking_type_id': dropship_picking_type.id,
                        'location_id': location_id.id,
                        'location_dest_id': self.location_dest_id.id,
                    })
                    picking.action_confirm()

            else:
                print("else")
                location_id = self.env['stock.location'].search([('usage', '=', 'production')])
                print(dropship_picking_type.name)
                picking = self.env['stock.picking'].create({
                    'location_id': location_id.id,
                    'location_dest_id': self.location_dest_id.id,
                    'partner_id': self._uid,
                    'origin': self.origin,
                    'picking_type_id': dropship_picking_type.id,
                    'immediate_transfer': True,
                })
                for i in company.move_raw_ids:
                    # print(i.product_id.name)

                    move_receipt_1 = self.env['stock.move'].create({
                        'name': i.name,
                        'product_id': i.product_id.id,
                        'product_uom_qty': i.product_qty,
                        'quantity_done': company.product_qty,
                        'product_uom': i.product_id.uom_id.id,
                        'picking_id': picking.id,
                        'picking_type_id': dropship_picking_type.id,
                        'location_id': location_id.id,
                        'location_dest_id': self.location_dest_id.id,
                    })
                    print(move_receipt_1)
                    # move_line_paw = self.env['stock.move.line'].create({
                    #     'product_id': i.product_id.id,
                    #     'product_uom_id': i.product_id.uom_id.id,
                    #     'picking_id': picking.id,
                    #     'qty_done': company.product_qty,
                    #     'location_id': self.env.ref('stock.stock_location_suppliers').id,
                    #     'location_dest_id': self.env.ref('stock.stock_location_customers').id,
                    # })
                    # print('l',l)
                    picking.action_confirm()

    def button_qc_sample(self):
        self.ensure_one()
        # self.write({'state': 'qc'})
        print("QC Sample")

        return {
            'name': _('QC Sample'),
            'view_mode': 'form',
            'res_model': 'stock.scrap',
            'view_id': self.env.ref('stock.stock_scrap_form_view2').id,
            'type': 'ir.actions.act_window',
            'context': {'default_production_id': self.id,
                        'product_ids': (self.move_raw_ids.filtered(lambda x: x.state not in ('done', 'cancel')) | self.move_finished_ids.filtered(lambda x: x.state == 'done')).mapped('product_id').ids,
                        'default_company_id': self.company_id.id
                        },
            'target': 'new',
        }


    def action_internal(self):
        values_to_create = []
        active_id = self.id
        print("active_id",active_id)

        for production in self:
            points = self.env['quality.point'].search([('title', '=', 'RRM Quality Points')],limit=1)
            if points:
                points = self.env['quality.point'].search([('title','=','RRM Quality Points')],limit=1)
                picking_type_id = self.env['stock.picking.type'].search([('sequence_code', '=', 'RRM')],limit=1)
                test_type_id = self.env['quality.point.test_type'].search([('name', '=', 'Pass - Fail')])
                team_id = self.env['quality.alert.team'].search([('name', '=', 'Main Quality Team')])
                name = self.name
                picking_id = self.env['stock.picking'].search([('origin', '=', self.origin)])
                quality_check_data = {
                    'picking_id': picking_id.id,
                    'product_id': self.product_id.id,
                    'point_id': points.id,
                    'company_id': self.company_id.id,
                    'team_id': team_id.id,
                    'test_type_id': test_type_id.id,
                    'origin': self.origin,
                }
                k = self.env['quality.check'].create(quality_check_data)
            else:
                picking_type_id=self.env['stock.picking.type'].search([('sequence_code','=','RRM')],limit=1)
                test_type_id = self.env['quality.point.test_type'].search([('name','=','Pass - Fail')])

                l=self.env['quality.point'].create({
                    'product_id': self.product_id.id,
                    'product_tmpl_id': self.product_id.product_tmpl_id.id,
                    'title': "RRM Quality Points",
                    'picking_type_id': picking_type_id.id,
                    'test_type_id': test_type_id.id
                })

                team_id = self.env['quality.alert.team'].search([('name', '=', 'Main Quality Team')])

                name=self.name
                picking_id = self.env['stock.picking'].search([('origin', '=', self.origin)],limit=1)

                quality_check_data = {
                    'picking_id': picking_id.id,
                    'product_id': self.product_id.id,
                    'point_id':points.id,
                    'company_id': self.company_id.id,
                    'team_id': team_id.id,
                    'test_type_id': test_type_id.id,
                    'origin': self.origin,
                }
                k=self.env['quality.check'].create(quality_check_data)



    def action_pass(self):
        scrapobj = self.env['stock.scrap'].search(
            [('production_id', '=', self.id), ('product_id', '=', self.product_id.id)])

        location_id = self.env['stock.location'].search([('usage', '=', 'production')])

        total = 0
        for i in scrapobj:
            total = total + i.scrap_qty
        for company in self:
            self.write({'state': 'transfer'})
            picking = self.env['stock.picking'].create({
                'location_id': location_id.id,
                'location_dest_id': company.location_dest_id.id,
                'picking_type_id': self.env.ref('stock.picking_type_internal').id,
                'origin': self.origin,
            })
            for i in company:
                move_receipt_1 = self.env['stock.move'].create({
                    'name': i.name,
                    'product_id': i.product_id.id,
                    'product_uom_qty': company.product_qty - total,
                    'quantity_done': company.product_qty - total,
                    'product_uom': i.product_id.uom_id.id,
                    'picking_id': picking.id,
                    'picking_type_id': self.env.ref('stock.picking_type_internal').id,
                    'location_id': location_id.id,
                    'location_dest_id': company.location_dest_id.id,
                })
                # k=self.env['stock.move.line'].create({
                #     'move_id': move_receipt_1.id,
                #     'picking_id': move_receipt_1.picking_id.id,
                #     'product_id': move_receipt_1.product_id.id,
                #     'location_id': move_receipt_1.location_id.id,
                #     'location_dest_id': move_receipt_1.location_dest_id.id,
                #     'product_uom_qty': company.product_qty - total,
                #     'product_uom_id':move_receipt_1.product_id.uom_id.id,
                #     'qty_done': company.product_qty - total,
                # })

            picking.action_confirm()
            picking.button_validate()


    def action_confirm(self):
        sequence = self.env['stock.picking'].search([
            ('origin', '=', self.origin)],limit=1)
        print(sequence.origin)

        if sequence.state in ['done']:
            self._check_company()
            for production in self:
                if not production.move_raw_ids:
                    raise UserError(_("Add some materials to consume before marking this MO as to do."))
                for move_raw in production.move_raw_ids:
                    move_raw.write({
                        'group_id': production.procurement_group_id.id,
                        'unit_factor': move_raw.product_uom_qty / production.product_qty,
                        'reference': production.name,  # set reference when MO name is different than 'New'
                    })
                production._generate_finished_moves()
                production.move_raw_ids._adjust_procure_method()
                (production.move_raw_ids | production.move_finished_ids)._action_confirm()
            return True
        else:
            raise UserError(_("Request for Raw Material is not Validated yet."))





class StockScrap(models.Model):
    _inherit = 'stock.scrap'


    def _get_default_scrap_location_id(self):
        company_id = self.env.context.get('default_company_id') or self.env.company.id
        return self.env['stock.location'].search([('scrap_location', '=', True), ('company_id', 'in', [company_id, False])], limit=1).id


    scrap_location_id = fields.Many2one(
        'stock.location', 'Scrap Location', default=_get_default_scrap_location_id,
        domain="[('company_id', 'in', [company_id, False])]", required=True,
        states={'done': [('readonly', True)]}, check_company=True)

class QualityCheck(models.Model):
    _inherit = "quality.check"

    origin=fields.Char("Origin")



    def do_fail(self):
        mrpobj = self.env['mrp.production'].search([('origin', '=', self.origin)])
        print(mrpobj.picking_type_id.name)
        self.write({
            'quality_state': 'fail',
            'user_id': self.env.user.id,
            'control_date': datetime.now()})
        self.redirect_after_pass_fail()
        scrap = self.env['stock.scrap'].create({
            'product_id': self.product_id.id,
            'product_uom_id': self.product_id.uom_id.id,
            'scrap_qty': mrpobj.product_qty,
            'picking_id': self.picking_id.id,
            'origin': self.origin
        })
        scrap.action_validate()
        # return {
        #     'name': _('QC Sample'),
        #     'view_mode': 'form',
        #     'res_model': 'stock.scrap',
        #     'view_id': self.env.ref('stock.stock_scrap_form_view2').id,
        #     'type': 'ir.actions.act_window',
        #     'context': {'default_product_id': self.product_id.id,
        #                 'default_company_id': self.company_id.id
        #                 },
        #     'target': 'new',
        # }


    def do_pass(self):
        mrpobj = self.env['mrp.production'].search([('origin', '=',self.origin)])

        print("mrpobj",mrpobj.origin)
        mrpobj.action_pass()
        self.write({'quality_state': 'pass',
                    'user_id': self.env.user.id,
                    'control_date': datetime.now()})
        return self.redirect_after_pass_fail()
