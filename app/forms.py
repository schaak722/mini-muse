from flask_wtf import FlaskForm
from wtforms import StringField, DecimalField, DateField, TextAreaField, SelectField, PasswordField
from wtforms.validators import DataRequired, Optional, Length, NumberRange

class LoginForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Length(max=255)])
    password = PasswordField("Password", validators=[DataRequired()])

class ItemForm(FlaskForm):
    user_item_id = StringField("User Item ID", validators=[DataRequired(), Length(max=80)])
    order_number = StringField("Order Number", validators=[DataRequired(), Length(max=80)])
    order_date = DateField("Order Date", validators=[DataRequired()])
    arrival_date = DateField("Arrival Date", validators=[DataRequired()])
    company_name = StringField("Company Name", validators=[DataRequired(), Length(max=255)])
    brand = StringField("Brand", validators=[DataRequired(), Length(max=255)])
    item_description = StringField("Item Description", validators=[DataRequired(), Length(max=500)])
    sku = StringField("SKU", validators=[DataRequired(), Length(max=120)])
    net_unit_cost = DecimalField("Net Unit Cost", validators=[DataRequired(), NumberRange(min=0)])
    freight_net = DecimalField("Freight (Net)", validators=[DataRequired(), NumberRange(min=0)])
    vat_rate = DecimalField("VAT Rate (e.g. 0.18)", validators=[DataRequired(), NumberRange(min=0, max=1)])

class SaleForm(FlaskForm):
    item_pk_id = StringField("Item PK ID", validators=[DataRequired(), Length(max=80)])
    sale_date = DateField("Sale Date", validators=[DataRequired()])
    item_selling_price_gross = DecimalField("Selling Price (Gross)", validators=[DataRequired(), NumberRange(min=0)])

    discount_type = SelectField("Discount Type", choices=[("", "None"), ("PERCENT", "Percent"), ("AMOUNT", "Amount")], validators=[Optional()])
    discount_value = DecimalField("Discount Value", validators=[Optional(), NumberRange(min=0)])
    discount_amount_gross = DecimalField("Discount Amount (Gross)", validators=[Optional(), NumberRange(min=0)])

    delivery_fee_charged_gross = DecimalField("Delivery Fee Charged (Gross)", validators=[Optional(), NumberRange(min=0)])
    packaging_net = DecimalField("Packaging (Net)", validators=[Optional(), NumberRange(min=0)])
    delivery_cost_net = DecimalField("Delivery Cost (Net)", validators=[Optional(), NumberRange(min=0)])
    other_cost_net = DecimalField("Other Cost (Net)", validators=[Optional(), NumberRange(min=0)])
    notes = TextAreaField("Notes", validators=[Optional()])

class ReverseSaleForm(FlaskForm):
    reason = TextAreaField("Reason", validators=[DataRequired(), Length(min=3, max=500)])

