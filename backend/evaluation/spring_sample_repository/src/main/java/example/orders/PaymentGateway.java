package example.orders;

public interface PaymentGateway {
    String charge(String orderId);
}
