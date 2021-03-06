from odoo import models, fields, api,_

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

    state = fields.Selection(selection_add=[('raw', 'Request Raw Material'),('qc', 'QC Sample')])

    def action_request(self):
        dropship_vals = []
        print("HEllo")
        self.write({'state': 'raw'})
        for company in self:
            sequence = self.env['ir.sequence'].search([
                ('code', '=', 'stock.dropshipping')])
            dropship_picking_type = self.env['stock.picking.type'].search([
                ('name', '=', 'Request Raw Material')],limit=1)
            product_car = self.env['product.product'].search([
                ('id', '=', company.product_id.id)], limit=1)
            print(dropship_picking_type.name)
            dropship_vals.append({
                'name': 'Request Raw Material',
                'warehouse_id':self.env.user.company_id.id,
                'sequence_id': sequence.id,
                'code': 'internal',
                'default_location_src_id': self.env.ref('stock.stock_location_suppliers').id,
                'default_location_dest_id': self.env.ref('stock.stock_location_customers').id,
                'sequence_code': 'RRM',

            })
            if dropship_vals:

                self.env['stock.picking.type'].create(dropship_vals)
                # outgoing_shipment = self.env['stock.picking'].create({
                #     'picking_type_id': dropship_picking_type.id,
                #     'location_id': self.env.ref('stock.stock_location_stock').id,
                #     'location_dest_id': self.env.ref('stock.stock_location_customers').id,
                #     'state':'confirmed',
                #     'move_lines': [(0, 0, {
                #         'name': 'Request Raw Material',
                #         'product_id': company.product_id.id,
                #         'product_uom_qty': company.product_qty,
                #         'product_uom': product_car.uom_id.id,
                #         'location_id': self.env.ref('stock.stock_location_stock').id,
                #         'location_dest_id': self.env.ref('stock.stock_location_customers').id})]
                # })
                # outgoing_shipment.action_confirm()

    def button_qc_sample(self):
        self.ensure_one()
        self.write({'state': 'qc'})
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



