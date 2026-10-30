package example;

public class CommerceService {
    public void processPayment(String paymentId) {
        System.out.println(paymentId);
    }

    public void createOrder(String orderId) {
        System.out.println(orderId);
    }

    public void reserveStock(String sku) {
        System.out.println(sku);
    }

    public void calculateShipping(String destination) {
        System.out.println(destination);
    }

    public void applyDiscount(String orderId) {
        System.out.println(orderId);
    }

    public void validateCoupon(String couponCode) {
        System.out.println(couponCode);
    }

    public void refundPayment(String paymentId) {
        System.out.println(paymentId);
    }

    public void cancelOrder(String orderId) {
        System.out.println(orderId);
    }

    public void updateInventory(String sku) {
        System.out.println(sku);
    }

    public void sendReceipt(String customerEmail) {
        System.out.println(customerEmail);
    }

    public void findCustomer(String customerId) {
        System.out.println(customerId);
    }

    public void auditTransaction(String transactionId) {
        System.out.println(transactionId);
    }
}
