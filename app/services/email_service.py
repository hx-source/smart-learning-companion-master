"""
邮件服务
"""
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from flask import current_app

class EmailService:
    @staticmethod
    def send_email(to_email, subject, html_content):
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = current_app.config['QQ_EMAIL']
            msg['To'] = to_email

            html_part = MIMEText(html_content, 'html', 'utf-8')
            msg.attach(html_part)

            with smtplib.SMTP_SSL('smtp.qq.com', 465) as server:
                server.login(current_app.config['QQ_EMAIL'], current_app.config['QQ_EMAIL_AUTH_CODE'])
                server.sendmail(current_app.config['QQ_EMAIL'], to_email, msg.as_string())
            return True, "邮件发送成功"
        except Exception as e:
            return False, str(e)

    @staticmethod
    def send_verification_email(email, username, code, base_url):
        subject = "智学伴 - 邮箱验证"
        verify_link = f"{base_url}/api/auth/verify-email?code={code}"

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {{ font-family: Arial, sans-serif; background-color: #f5f5f5; margin: 0; padding: 20px; }}
                .container {{ max-width: 600px; margin: 0 auto; background: white; border-radius: 10px; padding: 30px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
                h1 {{ color: #4A90E2; margin-bottom: 20px; }}
                .code {{ font-size: 32px; font-weight: bold; color: #4A90E2; text-align: center; padding: 20px; background: #f0f7ff; border-radius: 8px; margin: 20px 0; letter-spacing: 5px; }}
                .footer {{ color: #888; font-size: 12px; margin-top: 30px; text-align: center; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>📚 智学伴 - 邮箱验证</h1>
                <p>亲爱的 <strong>{username}</strong>，您好！</p>
                <p>感谢您注册智学伴平台，您的注册验证码是：</p>
                <div class="code">{code}</div>
                <p>验证码有效期为 <strong>30分钟</strong>，请及时完成验证。</p>
                <p>如果您没有注册智学伴账号，请忽略此邮件。</p>
                <div class="footer">
                    <p>智学伴 - AI个性化学习伴侣</p>
                    <p>这是一封系统自动发送的邮件，请勿回复。</p>
                </div>
            </div>
        </body>
        </html>
        """
        return EmailService.send_email(email, subject, html_content)

    @staticmethod
    def send_password_reset_code(email, username, code):
        subject = "智学伴 - 密码重置验证码"
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {{ font-family: Arial, sans-serif; background-color: #f5f5f5; margin: 0; padding: 20px; }}
                .container {{ max-width: 600px; margin: 0 auto; background: white; border-radius: 10px; padding: 30px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
                h1 {{ color: #E94B3C; margin-bottom: 20px; }}
                .code {{ font-size: 36px; font-weight: bold; color: #E94B3C; text-align: center; padding: 20px; background: #fff5f5; border-radius: 8px; margin: 20px 0; letter-spacing: 8px; }}
                .warning {{ background: #FFF3CD; padding: 15px; border-radius: 5px; margin: 20px 0; color: #856404; }}
                .footer {{ color: #888; font-size: 12px; margin-top: 30px; text-align: center; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🔐 智学伴 - 密码重置</h1>
                <p>亲爱的 <strong>{username}</strong>，您好！</p>
                <p>我们收到了您的密码重置请求，您的验证码是：</p>
                <div class="code">{code}</div>
                <p>验证码有效期为 <strong>10分钟</strong>，请在页面输入验证码完成验证。</p>
                <div class="warning">
                    <strong>⚠️ 安全提示：</strong><br>
                    • 请勿将验证码泄露给他人<br>
                    • 如果您没有发起密码重置，请忽略此邮件
                </div>
                <div class="footer">
                    <p>智学伴 - AI个性化学习伴侣</p>
                    <p>这是一封系统自动发送的邮件，请勿回复。</p>
                </div>
            </div>
        </body>
        </html>
        """
        return EmailService.send_email(email, subject, html_content)

    @staticmethod
    def send_password_reset_email(email, username, reset_link):
        subject = "智学伴 - 密码重置"
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {{ font-family: Arial, sans-serif; background-color: #f5f5f5; margin: 0; padding: 20px; }}
                .container {{ max-width: 600px; margin: 0 auto; background: white; border-radius: 10px; padding: 30px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
                h1 {{ color: #E94B3C; margin-bottom: 20px; }}
                .button {{ display: inline-block; padding: 15px 30px; background: #E94B3C; color: white; text-decoration: none; border-radius: 5px; margin: 20px 0; }}
                .warning {{ background: #FFF3CD; padding: 15px; border-radius: 5px; margin: 20px 0; color: #856404; }}
                .footer {{ color: #888; font-size: 12px; margin-top: 30px; text-align: center; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🔐 智学伴 - 密码重置</h1>
                <p>亲爱的 <strong>{username}</strong>，您好！</p>
                <p>我们收到了您的密码重置请求，请点击以下按钮重置密码：</p>
                <a href="{reset_link}" class="button">重置密码</a>
                <div class="warning">
                    <strong>⚠️ 安全提示：</strong><br>
                    • 链接有效期为 <strong>1小时</strong><br>
                    • 请勿将链接泄露给他人<br>
                    • 如果您没有发起密码重置，请忽略此邮件
                </div>
                <div class="footer">
                    <p>智学伴 - AI个性化学习伴侣</p>
                    <p>这是一封系统自动发送的邮件，请勿回复。</p>
                </div>
            </div>
        </body>
        </html>
        """
        return EmailService.send_email(email, subject, html_content)
