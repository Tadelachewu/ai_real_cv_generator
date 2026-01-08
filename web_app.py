from flask import Flask, render_template, redirect, request, flash, url_for
from payment_gate import is_payment_required
import os
import smtplib
from email.message import EmailMessage

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-secret')


@app.route('/contact')
def contact():
    phone = '0949847581'
    email = 'tade2024bdu@gmail.com'
    payment_enabled = is_payment_required()
    cbe_account = '1000261725456'
    amount_etb = 10
    return render_template('contact.html', phone=phone, email=email,
                           payment_enabled=payment_enabled,
                           cbe_account=cbe_account,
                           amount_etb=amount_etb)



@app.route('/contact/send', methods=['POST'])
def contact_send():
    name = request.form.get('name', '').strip()
    sender = request.form.get('email', '').strip()
    subject = request.form.get('subject', '').strip()
    message = request.form.get('message', '').strip()

    if not sender or not subject or not message:
        flash('Please provide your email, subject and message.', 'error')
        return redirect(url_for('contact'))

    # Build email
    full_body = f"From: {name} <{sender}>\n\n{message}"
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = os.environ.get('FROM_EMAIL', 'no-reply@example.com')
    msg['To'] = os.environ.get('CONTACT_RECEIVER_EMAIL', 'tade2024bdu@gmail.com')
    msg.set_content(full_body)

    smtp_host = os.environ.get('SMTP_HOST')
    smtp_port = int(os.environ.get('SMTP_PORT', '0') or 0)
    smtp_user = os.environ.get('SMTP_USER')
    smtp_pass = os.environ.get('SMTP_PASS')

    try:
        if smtp_host and smtp_port:
            if smtp_user and smtp_pass:
                server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=10)
                server.login(smtp_user, smtp_pass)
            else:
                server = smtplib.SMTP(smtp_host, smtp_port, timeout=10)
                server.starttls()
            server.send_message(msg)
            server.quit()
            flash('Message sent successfully. We will contact you shortly.', 'success')
        else:
            # Fallback: write message to a local file for manual pickup
            out_dir = os.path.join(os.path.dirname(__file__), 'sent_emails')
            os.makedirs(out_dir, exist_ok=True)
            idx = len(os.listdir(out_dir)) + 1
            path = os.path.join(out_dir, f'message_{idx}.eml')
            with open(path, 'w', encoding='utf-8') as f:
                f.write(f"To: {msg['To']}\nSubject: {msg['Subject']}\n\n{full_body}")
            flash('SMTP not configured; message saved locally for review.', 'warning')
    except Exception as e:
        flash(f'Failed to send message: {e}', 'error')

    return redirect(url_for('contact'))


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
