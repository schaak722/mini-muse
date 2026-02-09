"""
Google Analytics 4 API Client
Fetches analytics data for dashboard metrics
"""
import os
import json
from datetime import date, timedelta
from functools import lru_cache
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    RunReportRequest,
    Dimension,
    Metric,
    DateRange,
)
from google.oauth2 import service_account


def get_ga4_client():
    """
    Initialize GA4 client with service account credentials from environment
    """
    credentials_json = os.getenv('GOOGLE_ANALYTICS_CREDENTIALS')
    
    if not credentials_json:
        return None
    
    try:
        credentials_dict = json.loads(credentials_json)
        credentials = service_account.Credentials.from_service_account_info(
            credentials_dict,
            scopes=['https://www.googleapis.com/auth/analytics.readonly']
        )
        return BetaAnalyticsDataClient(credentials=credentials)
    except Exception as e:
        print(f"Error initializing GA4 client: {e}")
        return None


@lru_cache(maxsize=50)
def get_ga4_metrics(start_date: date, end_date: date) -> dict:
    """
    Fetch GA4 metrics for dashboard
    
    Returns:
        dict: {
            'total_views': int,
            'unique_visitors': int,
            'avg_session_duration': float (seconds),
            'new_users': int,
            'returning_users': int,
            'top_pages': [{'page': str, 'views': int}, ...]
        }
    """
    client = get_ga4_client()
    property_id = os.getenv('GA4_PROPERTY_ID')
    
    if not client or not property_id:
        # Return dummy data if GA not configured
        return {
            'total_views': 0,
            'unique_visitors': 0,
            'avg_session_duration': 0.0,
            'new_users': 0,
            'returning_users': 0,
            'top_pages': [],
            'previous_views': 0
        }
    
    try:
        # Calculate previous period for trend
        period_days = (end_date - start_date).days + 1
        prev_end = start_date - timedelta(days=1)
        prev_start = prev_end - timedelta(days=period_days - 1)
        
        # Request 1: Overall metrics
        request = RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[
                DateRange(
                    start_date=start_date.strftime('%Y-%m-%d'),
                    end_date=end_date.strftime('%Y-%m-%d')
                )
            ],
            metrics=[
                Metric(name="screenPageViews"),
                Metric(name="totalUsers"),
                Metric(name="averageSessionDuration"),
            ],
        )
        
        response = client.run_report(request)
        
        # Extract metrics
        if response.rows:
            row = response.rows[0]
            total_views = int(row.metric_values[0].value)  # screenPageViews
            unique_visitors = int(row.metric_values[1].value)  # totalUsers
            avg_session_duration = float(row.metric_values[2].value)  # averageSessionDuration
            # new_users and returning_users will be set by dimension query below
        else:
            total_views = unique_visitors = 0
            avg_session_duration = 0.0
        
        # Request 2: Previous period views for trend
        prev_request = RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[
                DateRange(
                    start_date=prev_start.strftime('%Y-%m-%d'),
                    end_date=prev_end.strftime('%Y-%m-%d')
                )
            ],
            metrics=[Metric(name="screenPageViews")],
        )
        
        prev_response = client.run_report(prev_request)
        previous_views = int(prev_response.rows[0].metric_values[0].value) if prev_response.rows else 0
        
        # Request 2.5: New vs Returning (using dimension)
        new_vs_returning_request = RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[
                DateRange(
                    start_date=start_date.strftime('%Y-%m-%d'),
                    end_date=end_date.strftime('%Y-%m-%d')
                )
            ],
            dimensions=[Dimension(name="newVsReturning")],
            metrics=[Metric(name="activeUsers")],
        )
        
        new_vs_returning_response = client.run_report(new_vs_returning_request)
        
        # Parse new vs returning
        actual_new_users = 0
        actual_returning_users = 0
        
        for row in new_vs_returning_response.rows:
            user_type = row.dimension_values[0].value  # "new" or "returning"
            count = int(row.metric_values[0].value)
            
            if user_type == "new":
                actual_new_users = count
            elif user_type == "returning":
                actual_returning_users = count
        
        # Override the calculated values with actual values
        new_users = actual_new_users
        returning_users = actual_returning_users
        
        # Request 3: Top pages
        pages_request = RunReportRequest(
            property=f"properties/{property_id}",
            date_ranges=[
                DateRange(
                    start_date=start_date.strftime('%Y-%m-%d'),
                    end_date=end_date.strftime('%Y-%m-%d')
                )
            ],
            dimensions=[Dimension(name="pagePath")],
            metrics=[Metric(name="screenPageViews")],
            limit=10,
            order_bys=[{
                'metric': {'metric_name': 'screenPageViews'},
                'desc': True
            }]
        )
        
        pages_response = client.run_report(pages_request)
        
        # Extract top pages
        top_pages = []
        max_views = 0
        
        for row in pages_response.rows:
            page_path = row.dimension_values[0].value
            views = int(row.metric_values[0].value)
            
            if not max_views:
                max_views = views
            
            top_pages.append({
                'page': page_path,
                'views': views,
                'percentage': (views / max_views * 100) if max_views > 0 else 0
            })
        
        return {
            'total_views': total_views,
            'unique_visitors': unique_visitors,
            'avg_session_duration': avg_session_duration,
            'new_users': new_users,
            'returning_users': returning_users,
            'top_pages': top_pages,
            'previous_views': previous_views
        }
        
    except Exception as e:
        print(f"Error fetching GA4 data: {e}")
        # Return empty data on error
        return {
            'total_views': 0,
            'unique_visitors': 0,
            'avg_session_duration': 0.0,
            'new_users': 0,
            'returning_users': 0,
            'top_pages': [],
            'previous_views': 0
        }


def format_duration(seconds: float) -> str:
    """
    Format duration in seconds to readable format (e.g., "3m 42s")
    """
    if seconds < 60:
        return f"{int(seconds)}s"
    
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    
    if minutes < 60:
        return f"{minutes}m {secs}s"
    
    hours = int(minutes // 60)
    mins = int(minutes % 60)
    return f"{hours}h {mins}m"
