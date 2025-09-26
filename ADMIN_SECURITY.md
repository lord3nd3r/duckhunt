# Enhanced Admin Configuration

For better security, update your `config.json` to use hostmask-based admin authentication:

## Current (Less Secure) - Nick Only:
```json
{
    "admins": [
        "peorth",
        "computertech", 
        "colby"
    ]
}
```

## Recommended (More Secure) - Hostmask Based:
```json
{
    "admins": [
        {
            "nick": "peorth",
            "hostmask": "peorth!*@trusted.domain.com"
        },
        {
            "nick": "computertech", 
            "hostmask": "computertech!*@*.isp.net"
        },
        {
            "nick": "colby",
            "hostmask": "colby!user@192.168.*.*"
        }
    ]
}
```

## Migration Notes:
- The bot supports both formats for backward compatibility
- Nick-only authentication generates security warnings in logs
- Hostmask patterns use shell-style wildcards (* and ?)
- Consider using registered nick services for additional security

## Security Benefits:
- Prevents nick spoofing attacks
- Allows IP/hostname restrictions
- Provides audit logging of admin access
- Maintains backward compatibility during migration