"""
KPI Calculator for Dashboard
Calculates revenue, profit, and other metrics with date filtering and caching
"""
from datetime import datetime, date, timedelta
from sqlalchemy import func, and_
from functools import lru_cache
from app.extensions import db
from app.models import Item, Sale

# Import GA4 client (will be None if not configured)
try:
    from app.utils.ga4_client import get_ga4_metrics, format_duration
    GA4_AVAILABLE = True
except ImportError:
    GA4_AVAILABLE = False
    get_ga4_metrics = None
    format_duration = None


def get_date_range(period: str, custom_from: str = None, custom_to: str = None):
    """
    Calculate start and end dates based on period
    
    Args:
        period: 'last_7_days', 'last_month', 'ytd', 'custom'
        custom_from: Start date for custom range (YYYY-MM-DD)
        custom_to: End date for custom range (YYYY-MM-DD)
    
    Returns:
        tuple: (start_date, end_date, previous_start, previous_end)
    """
    today = date.today()
    
    if period == 'last_7_days':
        end_date = today
        start_date = today - timedelta(days=6)  # 7 days including today
        period_days = 7
        
    elif period == 'last_month':
        # Previous calendar month
        first_of_this_month = today.replace(day=1)
        end_date = first_of_this_month - timedelta(days=1)  # Last day of previous month
        start_date = end_date.replace(day=1)  # First day of previous month
        period_days = (end_date - start_date).days + 1
        
    elif period == 'ytd':
        # Year to date
        start_date = date(today.year, 1, 1)
        end_date = today
        period_days = (end_date - start_date).days + 1
        
    elif period == 'custom':
        if not custom_from or not custom_to:
            # Default to last 7 days if custom dates not provided
            end_date = today
            start_date = today - timedelta(days=6)
            period_days = 7
        else:
            start_date = datetime.strptime(custom_from, '%Y-%m-%d').date()
            end_date = datetime.strptime(custom_to, '%Y-%m-%d').date()
            period_days = (end_date - start_date).days + 1
    else:
        # Default to last 7 days
        end_date = today
        start_date = today - timedelta(days=6)
        period_days = 7
    
    # Calculate previous period for comparison
    previous_end = start_date - timedelta(days=1)
    previous_start = previous_end - timedelta(days=period_days - 1)
    
    return start_date, end_date, previous_start, previous_end


@lru_cache(maxsize=100)
def calculate_revenue(start_date: date, end_date: date) -> float:
    """
    Calculate net revenue (after discounts, excluding delivery fee charged)
    
    Revenue = SUM(item_selling_price_gross - discount_amount_gross)
    """
    result = db.session.query(
        func.sum(
            Sale.item_selling_price_gross - 
            func.coalesce(Sale.discount_amount_gross, 0)
        )
    ).filter(
        and_(
            Sale.sale_date >= start_date,
            Sale.sale_date <= end_date
        )
    ).scalar()
    
    return float(result) if result else 0.0


@lru_cache(maxsize=100)
def calculate_profit(start_date: date, end_date: date) -> float:
    """
    Calculate profit (revenue minus all costs, excluding delivery fee charged)
    
    Profit = Revenue - (item cost + freight + packaging + delivery cost + other costs)
    """
    result = db.session.query(
        func.sum(Sale.item_profit)
    ).filter(
        and_(
            Sale.sale_date >= start_date,
            Sale.sale_date <= end_date
        )
    ).scalar()
    
    return float(result) if result else 0.0


def calculate_items_in_stock() -> dict:
    """
    Calculate current items in stock and their total value at cost
    
    Returns:
        dict: {'count': int, 'value': float}
    """
    result = db.session.query(
        func.count(Item.pk_id),
        func.sum(Item.net_unit_cost + func.coalesce(Item.freight_net, 0))
    ).filter(
        Item.status == 'IN_STOCK'
    ).first()
    
    count = int(result[0]) if result[0] else 0
    value = float(result[1]) if result[1] else 0.0
    
    return {'count': count, 'value': value}


@lru_cache(maxsize=100)
def calculate_items_sold(start_date: date, end_date: date) -> int:
    """
    Calculate total items sold in period
    """
    result = db.session.query(
        func.count(Sale.pk_id)
    ).filter(
        and_(
            Sale.sale_date >= start_date,
            Sale.sale_date <= end_date
        )
    ).scalar()
    
    return int(result) if result else 0


@lru_cache(maxsize=100)
def get_most_sold_items(start_date: date, end_date: date, limit: int = 5) -> list:
    """
    Get top items by quantity sold with revenue
    
    Returns:
        list: [{'name': str, 'quantity': int, 'revenue': float}, ...]
    """
    results = db.session.query(
        Item.item_description,
        func.count(Sale.pk_id).label('quantity'),
        func.sum(
            Sale.item_selling_price_gross - 
            func.coalesce(Sale.discount_amount_gross, 0)
        ).label('revenue')
    ).join(
        Sale, Sale.item_pk_id == Item.pk_id
    ).filter(
        and_(
            Sale.sale_date >= start_date,
            Sale.sale_date <= end_date
        )
    ).group_by(
        Item.item_description
    ).order_by(
        func.count(Sale.pk_id).desc()
    ).limit(limit).all()
    
    items = []
    max_quantity = int(results[0][1]) if results and results[0][1] else 1
    
    for description, quantity, revenue in results:
        # Convert to proper types immediately
        quantity_int = int(quantity)
        revenue_float = float(revenue) if revenue else 0.0
        items.append({
            'name': description,
            'quantity': quantity_int,
            'revenue': revenue_float,
            'percentage': (quantity_int / max_quantity * 100) if max_quantity > 0 else 0
        })
    
    return items


@lru_cache(maxsize=100)
def get_top_brands(start_date: date, end_date: date, limit: int = 5) -> list:
    """
    Get top brands by revenue with quantity
    
    Returns:
        list: [{'name': str, 'revenue': float, 'quantity': int}, ...]
    """
    results = db.session.query(
        Item.brand,
        func.sum(
            Sale.item_selling_price_gross - 
            func.coalesce(Sale.discount_amount_gross, 0)
        ).label('revenue'),
        func.count(Sale.pk_id).label('quantity')
    ).join(
        Sale, Sale.item_pk_id == Item.pk_id
    ).filter(
        and_(
            Sale.sale_date >= start_date,
            Sale.sale_date <= end_date
        )
    ).group_by(
        Item.brand
    ).order_by(
        func.sum(
            Sale.item_selling_price_gross - 
            func.coalesce(Sale.discount_amount_gross, 0)
        ).desc()
    ).limit(limit).all()
    
    brands = []
    # Convert to float immediately to avoid Decimal issues
    max_revenue = float(results[0][1]) if results and results[0][1] else 1.0
    
    for brand, revenue, quantity in results:
        # Convert revenue to float immediately
        revenue_float = float(revenue) if revenue else 0.0
        brands.append({
            'name': brand,
            'revenue': revenue_float,
            'quantity': int(quantity),
            'percentage': (revenue_float / max_revenue * 100) if max_revenue > 0 else 0
        })
    
    return brands


def calculate_trend(current_value: float, previous_value: float) -> dict:
    """
    Calculate percentage change and direction
    
    Returns:
        dict: {'percent': float, 'direction': 'up'|'down'|'neutral'}
    """
    if previous_value == 0:
        if current_value > 0:
            return {'percent': 100.0, 'direction': 'up'}
        else:
            return {'percent': 0.0, 'direction': 'neutral'}
    
    percent_change = ((current_value - previous_value) / previous_value) * 100
    
    if percent_change > 0:
        direction = 'up'
    elif percent_change < 0:
        direction = 'down'
    else:
        direction = 'neutral'
    
    return {
        'percent': abs(percent_change),
        'direction': direction
    }


def get_dashboard_kpis(period: str = 'last_7_days', custom_from: str = None, custom_to: str = None) -> dict:
    """
    Calculate all dashboard KPIs with caching
    
    Args:
        period: 'last_7_days', 'last_month', 'ytd', 'custom'
        custom_from: Start date for custom range (YYYY-MM-DD)
        custom_to: End date for custom range (YYYY-MM-DD)
    
    Returns:
        dict: All KPI data for dashboard
    """
    # Get date ranges
    start_date, end_date, prev_start, prev_end = get_date_range(period, custom_from, custom_to)
    
    # Current period metrics
    revenue_current = calculate_revenue(start_date, end_date)
    profit_current = calculate_profit(start_date, end_date)
    items_sold_current = calculate_items_sold(start_date, end_date)
    
    # Previous period metrics for trends
    revenue_previous = calculate_revenue(prev_start, prev_end)
    profit_previous = calculate_profit(prev_start, prev_end)
    items_sold_previous = calculate_items_sold(prev_start, prev_end)
    
    # Items in stock (not date-filtered)
    stock_data = calculate_items_in_stock()
    
    # Top items and brands
    most_sold = get_most_sold_items(start_date, end_date)
    top_brands = get_top_brands(start_date, end_date)
    
    # Google Analytics metrics (if configured)
    ga_metrics = None
    if GA4_AVAILABLE and get_ga4_metrics:
        try:
            ga_data = get_ga4_metrics(start_date, end_date)
            
            # Calculate trend
            ga_trend = calculate_trend(
                float(ga_data['total_views']),
                float(ga_data['previous_views'])
            )
            
            ga_metrics = {
                'total_views': ga_data['total_views'],
                'unique_visitors': ga_data['unique_visitors'],
                'avg_session_duration': ga_data['avg_session_duration'],
                'avg_session_duration_formatted': format_duration(ga_data['avg_session_duration']) if format_duration else '0s',
                'new_users': ga_data['new_users'],
                'returning_users': ga_data['returning_users'],
                'top_pages': ga_data['top_pages'],
                'trend': ga_trend
            }
        except Exception as e:
            print(f"Error fetching GA metrics: {e}")
            ga_metrics = None
    
    return {
        'period': period,
        'start_date': start_date.isoformat(),
        'end_date': end_date.isoformat(),
        'revenue': {
            'value': revenue_current,
            'trend': calculate_trend(revenue_current, revenue_previous)
        },
        'profit': {
            'value': profit_current,
            'trend': calculate_trend(profit_current, profit_previous)
        },
        'items_in_stock': stock_data,
        'items_sold': {
            'value': items_sold_current,
            'trend': calculate_trend(items_sold_current, items_sold_previous)
        },
        'most_sold_items': most_sold,
        'top_brands': top_brands,
        'ga_metrics': ga_metrics  # Will be None if GA not configured
    }
