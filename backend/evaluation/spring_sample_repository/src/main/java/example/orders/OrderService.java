package example.orders;

import org.springframework.stereotype.Service;

@Service
public class OrderService {
    private final OrderRepository repository;
    private final PaymentGateway payments;

    public OrderService(OrderRepository repository, PaymentGateway payments) {
        this.repository = repository;
        this.payments = payments;
    }

    public String placeOrder(String orderId) {
        String receipt = payments.charge(orderId);
        repository.save(orderId);
        return receipt;
    }

    public String cancelOrder(String orderId) {
        repository.delete(orderId);
        return orderId;
    }
}
