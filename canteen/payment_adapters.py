"""
Payment Gateway Adapters
Supports Mock (demo) and Real API modes
Toggle between modes in PaymentGatewayConfig settings
"""

import time
import uuid
import requests
from decimal import Decimal
from django.conf import settings


class PaymentAdapter:
    """Base payment adapter interface"""
    
    def process_payment(self, amount, reference=None, metadata=None):
        """Process a payment and return result"""
        raise NotImplementedError("Subclasses must implement process_payment")
    
    def verify_payment(self, transaction_id):
        """Verify payment status"""
        raise NotImplementedError("Subclasses must implement verify_payment")
    
    def refund_payment(self, transaction_id, amount=None):
        """Refund a payment"""
        raise NotImplementedError("Subclasses must implement refund_payment")


# ============================================
# GCASH ADAPTERS
# ============================================

class GCashMockAdapter(PaymentAdapter):
    """Mock GCash adapter for testing/demo"""
    
    def process_payment(self, amount, reference=None, metadata=None):
        """Simulate GCash payment processing"""
        # Simulate API delay
        time.sleep(1.5)
        
        # Generate mock transaction ID
        transaction_id = f'GCASH-MOCK-{uuid.uuid4().hex[:8].upper()}'
        
        return {
            'success': True,
            'transaction_id': transaction_id,
            'reference': reference or f'REF-{uuid.uuid4().hex[:6].upper()}',
            'amount': float(amount),
            'currency': 'PHP',
            'status': 'success',
            'payment_method': 'gcash',
            'timestamp': time.time(),
            'message': 'Mock GCash payment successful'
        }
    
    def verify_payment(self, transaction_id):
        """Mock payment verification"""
        time.sleep(0.5)
        return {
            'verified': True,
            'status': 'success',
            'transaction_id': transaction_id,
            'message': 'Mock verification successful'
        }
    
    def refund_payment(self, transaction_id, amount=None):
        """Mock refund"""
        time.sleep(1)
        return {
            'success': True,
            'refund_id': f'REFUND-{uuid.uuid4().hex[:8].upper()}',
            'transaction_id': transaction_id,
            'amount': amount,
            'message': 'Mock refund successful'
        }


class GCashRealAdapter(PaymentAdapter):
    """Real GCash API adapter"""
    
    def __init__(self, config):
        self.merchant_id = config.merchant_id
        self.api_key = config.api_key
        self.api_secret = config.api_secret
        self.base_url = 'https://api.gcash.com/v1'  # Replace with actual GCash API URL
    
    def process_payment(self, amount, reference=None, metadata=None):
        """Process real GCash payment"""
        try:
            url = f'{self.base_url}/payments'
            headers = {
                'Authorization': f'Bearer {self.api_key}',
                'Content-Type': 'application/json'
            }
            payload = {
                'merchantId': self.merchant_id,
                'amount': float(amount),
                'currency': 'PHP',
                'referenceId': reference or f'TXN-{uuid.uuid4().hex[:8].upper()}',
                'metadata': metadata or {}
            }
            
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            return {
                'success': True,
                'transaction_id': data.get('transactionId'),
                'reference': data.get('referenceId'),
                'amount': data.get('amount'),
                'status': data.get('status'),
                'payment_method': 'gcash',
                'message': 'GCash payment processed successfully'
            }
            
        except requests.exceptions.RequestException as e:
            return {
                'success': False,
                'message': f'GCash API error: {str(e)}',
                'error_code': 'API_ERROR'
            }
    
    def verify_payment(self, transaction_id):
        """Verify real GCash payment"""
        try:
            url = f'{self.base_url}/payments/{transaction_id}'
            headers = {
                'Authorization': f'Bearer {self.api_key}'
            }
            
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            return {
                'verified': True,
                'status': data.get('status'),
                'transaction_id': transaction_id,
                'amount': data.get('amount')
            }
            
        except requests.exceptions.RequestException as e:
            return {
                'verified': False,
                'message': f'Verification failed: {str(e)}'
            }
    
    def refund_payment(self, transaction_id, amount=None):
        """Process real GCash refund"""
        try:
            url = f'{self.base_url}/refunds'
            headers = {
                'Authorization': f'Bearer {self.api_key}',
                'Content-Type': 'application/json'
            }
            payload = {
                'transactionId': transaction_id,
                'amount': float(amount) if amount else None
            }
            
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            return {
                'success': True,
                'refund_id': data.get('refundId'),
                'transaction_id': transaction_id,
                'amount': data.get('amount'),
                'message': 'Refund processed successfully'
            }
            
        except requests.exceptions.RequestException as e:
            return {
                'success': False,
                'message': f'Refund failed: {str(e)}'
            }


# ============================================
# MAYA ADAPTERS
# ============================================

class MayaMockAdapter(PaymentAdapter):
    """Mock Maya adapter for testing/demo"""
    
    def process_payment(self, amount, reference=None, metadata=None):
        """Simulate Maya QR payment"""
        time.sleep(1.5)
        
        transaction_id = f'MAYA-MOCK-{uuid.uuid4().hex[:8].upper()}'
        
        return {
            'success': True,
            'transaction_id': transaction_id,
            'reference': reference or f'MAYA-{uuid.uuid4().hex[:6].upper()}',
            'amount': float(amount),
            'currency': 'PHP',
            'status': 'success',
            'payment_method': 'maya',
            'qr_code': f'MAYA-QR-{uuid.uuid4().hex}',  # Mock QR code data
            'timestamp': time.time(),
            'message': 'Mock Maya payment successful'
        }
    
    def verify_payment(self, transaction_id):
        """Mock Maya verification"""
        time.sleep(0.5)
        return {
            'verified': True,
            'status': 'success',
            'transaction_id': transaction_id,
            'message': 'Mock Maya verification successful'
        }
    
    def refund_payment(self, transaction_id, amount=None):
        """Mock Maya refund"""
        time.sleep(1)
        return {
            'success': True,
            'refund_id': f'MAYA-REFUND-{uuid.uuid4().hex[:8].upper()}',
            'transaction_id': transaction_id,
            'amount': amount,
            'message': 'Mock Maya refund successful'
        }


class MayaRealAdapter(PaymentAdapter):
    """Real Maya API adapter"""
    
    def __init__(self, config):
        self.merchant_id = config.merchant_id
        self.public_key = config.api_key
        self.secret_key = config.api_secret
        self.base_url = 'https://api.paymaya.com/v1'  # Replace with actual Maya API URL
    
    def process_payment(self, amount, reference=None, metadata=None):
        """Process real Maya payment"""
        try:
            url = f'{self.base_url}/payments'
            headers = {
                'Authorization': f'Basic {self.public_key}',
                'Content-Type': 'application/json'
            }
            payload = {
                'totalAmount': {
                    'value': float(amount),
                    'currency': 'PHP'
                },
                'referenceNumber': reference or f'TXN-{uuid.uuid4().hex[:8].upper()}',
                'metadata': metadata or {}
            }
            
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            return {
                'success': True,
                'transaction_id': data.get('id'),
                'reference': data.get('referenceNumber'),
                'amount': data.get('totalAmount', {}).get('value'),
                'status': data.get('status'),
                'payment_method': 'maya',
                'message': 'Maya payment processed successfully'
            }
            
        except requests.exceptions.RequestException as e:
            return {
                'success': False,
                'message': f'Maya API error: {str(e)}',
                'error_code': 'API_ERROR'
            }
    
    def verify_payment(self, transaction_id):
        """Verify real Maya payment"""
        try:
            url = f'{self.base_url}/payments/{transaction_id}'
            headers = {
                'Authorization': f'Basic {self.public_key}'
            }
            
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            return {
                'verified': True,
                'status': data.get('status'),
                'transaction_id': transaction_id
            }
            
        except requests.exceptions.RequestException as e:
            return {
                'verified': False,
                'message': f'Verification failed: {str(e)}'
            }
    
    def refund_payment(self, transaction_id, amount=None):
        """Process real Maya refund"""
        try:
            url = f'{self.base_url}/payments/{transaction_id}/refunds'
            headers = {
                'Authorization': f'Basic {self.secret_key}',
                'Content-Type': 'application/json'
            }
            payload = {
                'totalAmount': {
                    'value': float(amount) if amount else None,
                    'currency': 'PHP'
                }
            }
            
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            return {
                'success': True,
                'refund_id': data.get('id'),
                'transaction_id': transaction_id,
                'message': 'Maya refund processed successfully'
            }
            
        except requests.exceptions.RequestException as e:
            return {
                'success': False,
                'message': f'Refund failed: {str(e)}'
            }


# ============================================
# MAYA TERMINAL ADAPTER
# ============================================

class MayaTerminalMockAdapter(PaymentAdapter):
    """Mock Maya Terminal for card payments"""
    
    def process_payment(self, amount, reference=None, metadata=None):
        """Simulate card payment via terminal"""
        time.sleep(2)  # Simulate card read + PIN entry
        
        transaction_id = f'TERMINAL-{uuid.uuid4().hex[:8].upper()}'
        
        # Simulate card types
        import random
        card_types = ['Visa', 'Mastercard', 'Maya Card']
        card_type = random.choice(card_types)
        last_4 = f'{random.randint(1000, 9999)}'

        return {
            'success': True,
            'transaction_id': transaction_id,
            'reference': reference or f'TERM-{uuid.uuid4().hex[:6].upper()}',
            'amount': float(amount),
            'currency': 'PHP',
            'status': 'approved',
            'payment_method': 'card',
            'card_type': card_type,
            'last_4_digits': last_4,
            'approval_code': f'APP-{random.randint(100000, 999999)}',
            'timestamp': time.time(),
            'message': f'Card payment approved - {card_type} ending in {last_4}'
        }
    
    def verify_payment(self, transaction_id):
        """Mock terminal verification"""
        return {
            'verified': True,
            'status': 'approved',
            'transaction_id': transaction_id
        }
    
    def refund_payment(self, transaction_id, amount=None):
        """Mock terminal refund"""
        time.sleep(1.5)
        return {
            'success': True,
            'refund_id': f'REFUND-{uuid.uuid4().hex[:8].upper()}',
            'transaction_id': transaction_id,
            'amount': amount,
            'message': 'Card refund successful'
        }


# ============================================
# PAYMENT GATEWAY FACTORY
# ============================================

class PaymentGatewayFactory:
    """Factory to get the appropriate payment adapter"""
    
    @staticmethod
    def get_adapter(gateway_type, terminal=False):
        """
        Get payment adapter based on gateway type and configuration
        
        Args:
            gateway_type: 'gcash' or 'maya'
            terminal: True for Maya terminal (card) payments
        
        Returns:
            PaymentAdapter instance (mock or real based on config)
        """
        from .models import PaymentGatewayConfig
        
        try:
            config = PaymentGatewayConfig.objects.get(gateway=gateway_type)
        except PaymentGatewayConfig.DoesNotExist:
            # Default to mock mode if no config exists
            print(f"Warning: No config found for {gateway_type}, using mock mode")
            if gateway_type == 'gcash':
                return GCashMockAdapter()
            elif gateway_type == 'maya' and terminal:
                return MayaTerminalMockAdapter()
            else:
                return MayaMockAdapter()
        
        # Return appropriate adapter based on config
        if gateway_type == 'gcash':
            if config.use_mock_mode:
                return GCashMockAdapter()
            else:
                return GCashRealAdapter(config)
        
        elif gateway_type == 'maya':
            if terminal and config.enable_terminal:
                if config.use_mock_mode:
                    return MayaTerminalMockAdapter()
                else:
                    # Return real terminal adapter when implemented
                    return MayaTerminalMockAdapter()  # Placeholder
            else:
                if config.use_mock_mode:
                    return MayaMockAdapter()
                else:
                    return MayaRealAdapter(config)
        
        else:
            raise ValueError(f"Unknown gateway type: {gateway_type}")


# ============================================
# CONVENIENCE FUNCTIONS
# ============================================

def process_gcash_payment(amount, reference=None, metadata=None):
    """Quick function to process GCash payment"""
    adapter = PaymentGatewayFactory.get_adapter('gcash')
    return adapter.process_payment(amount, reference, metadata)


def process_maya_payment(amount, reference=None, metadata=None):
    """Quick function to process Maya QR payment"""
    adapter = PaymentGatewayFactory.get_adapter('maya')
    return adapter.process_payment(amount, reference, metadata)


def process_card_payment(amount, reference=None, metadata=None):
    """Quick function to process card payment via Maya Terminal"""
    adapter = PaymentGatewayFactory.get_adapter('maya', terminal=True)
    return adapter.process_payment(amount, reference, metadata)
