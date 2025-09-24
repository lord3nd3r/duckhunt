#!/usr/bin/env python3

import json
import os

def test_config():
    """Test the config structure and values"""
    config_path = 'config.json'
    
    if not os.path.exists(config_path):
        print("❌ Config file not found")
        return
    
    with open(config_path, 'r') as f:
        config = json.load(f)
    
    print("🧪 Testing configuration structure...")
    
    # Test connection settings
    connection = config.get('connection', {})
    print(f"🌐 Server: {connection.get('server')}")
    print(f"🔌 Port: {connection.get('port')}")
    print(f"🤖 Nick: {connection.get('nick')}")
    print(f"🔒 SSL: {connection.get('ssl')}")
    
    # Check if password is set (not default)
    password = connection.get('password')
    password_set = password and password != "your_iline_password_here"
    print(f"🔑 Password configured: {'Yes' if password_set else 'No (default placeholder)'}")
    
    # Test SASL settings
    sasl = config.get('sasl', {})
    print(f"🔐 SASL enabled: {sasl.get('enabled')}")
    print(f"👤 SASL username: {sasl.get('username')}")
    
    # Check if SASL password is set (not default)
    sasl_password = sasl.get('password')
    sasl_password_set = sasl_password and sasl_password != "duckhunt//789//"
    print(f"🗝️ SASL password configured: {'Yes' if sasl_password_set else 'No (default placeholder)'}")
    
    # Test channels
    channels = connection.get('channels', [])
    print(f"📺 Channels to join: {channels}")
    
    print("\n✅ Configuration structure looks good!")
    
    if not password_set:
        print("⚠️ Warning: Server password is still set to placeholder value")
        print("   Update 'connection.password' if your server requires authentication")
    
    if sasl.get('enabled') and not sasl_password_set:
        print("⚠️ Warning: SASL is enabled but password is still placeholder")
        print("   Update 'sasl.password' with your actual NickServ password")

if __name__ == "__main__":
    test_config()